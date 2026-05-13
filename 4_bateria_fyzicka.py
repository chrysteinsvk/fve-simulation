import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# ===================== NASTAVENIA INVESTÍCIE =====================
FVE_FIXED_COST = 10447        # € (Panely, inštalácia, konštrukcia, bižutéria)
BATTERY_PRICE_PER_KWH = 500   # €/kWh (Súčasná cena za kapacitu)

# --- REINVESTÍCIA (v 15. roku simulácie) ---
REINVESTMENT_YEAR = 15
INVERTER_COST = 1500          # € (Cena nového striedača o 15 rokov)
BATTERY_REPLACEMENT_PRICE_PER_KWH = 300 # €/kWh (Predpokladaná znížená cena batérie o 15 rokov. 

# ===================== OSTATNÉ NASTAVENIA =====================
pv_file_1 = "data_13.json"
pv_file_2 = "data_90.json"
meters_file = "meters_731_measurement.json"
meter_id = "731"

YEARS = 25                    # Predĺžené na 25 rokov (životnosť FVE panelov)
CAPACITY_RANGE = range(0, 46) # Simulácia pre 0 až 45 kWh batérie
PRICE_CHANGE_RATE = 0.01      # 1 % ročná zmena ceny (pre scenáre)

# Parametre technológie
CHARGE_EFF = 0.95
DISCHARGE_EFF = 0.95
MAX_CHARGE_KW = 5
MAX_DISCHARGE_KW = 5
INITIAL_SOC_KWH = 0.0
BATTERY_DEGRADATION_RATE = 0.02 # 2 % pokles kapacity ročne
PV_DEGRADATION_RATE = 0.005      # 0.5 % pokles výroby panelov ročne

# Ceny energií
GRID_IMPORT_PRICE = 0.18      # €/kWh (východisková cena nákupu zo siete)
EXPORT_PRICE = 0.009          # €/kWh (9 €/MWh - výkupná cena)

# ===============================================================

def progress_iter(iterable, total=None, desc=None):
    if tqdm is not None:
        return tqdm(iterable, total=total, desc=desc)
    return iterable

def log(msg):
    print(msg, flush=True)

def load_pv(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame(data["outputs"]["hourly"])
    df["datetime"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M")
    df["P"] = pd.to_numeric(df["P"], errors="coerce")
    return df.set_index("datetime").sort_index()

def build_meter_ts(path, meter_id):
    with open(path, encoding="utf-8") as f:
        meters = json.load(f)
    rows = []
    for e in meters:
        if str(e.get("meterID")) != str(meter_id): continue
        base = pd.Timestamp(year=int(e["year"]), month=int(e["month"]), day=int(e["day"]))
        cons = e["consumption"]
        step = 60 if len(cons) == 24 else (30 if len(cons) == 48 else 15)
        for i, v in enumerate(cons):
            rows.append({"datetime": base + pd.Timedelta(minutes=i * step), "cons": pd.to_numeric(v, errors="coerce")})
    return pd.DataFrame(rows).set_index("datetime").sort_index()

def prepare_base_df():
    log("Načítavam PV a merané dáta...")
    pv1 = load_pv(pv_file_1)[["P"]].rename(columns={"P": "pv1"})
    pv2 = load_pv(pv_file_2)[["P"]].rename(columns={"P": "pv2"})
    pv1.index = pv1.index.floor("h")
    pv2.index = pv2.index.floor("h")
    pv = pv1.join(pv2, how="inner")
    pv["gen_kWh"] = (pv["pv1"] + pv["pv2"]) / 1000.0
    meter = build_meter_ts(meters_file, meter_id)
    cons_h = meter["cons"].resample("h").sum()
    df = pv.join(cons_h, how="inner").rename(columns={"cons": "cons_kWh"}).dropna()
    return df[["gen_kWh", "cons_kWh"]]

def simulate_battery(df, capacity_kwh, import_price):
    """Jadro simulácie energetických tokov v dome."""
    soc = min(INITIAL_SOC_KWH, capacity_kwh)
    res = []

    for r in df.itertuples():
        gen = r.gen_kWh
        cons = r.cons_kWh
        
        direct_use = min(gen, cons)
        surplus = gen - direct_use
        deficit = cons - direct_use
        
        export = 0.0
        grid_import = 0.0
        
        if surplus > 0:
            if capacity_kwh > 0:
                charge_space = capacity_kwh - soc
                to_charge = min(surplus, MAX_CHARGE_KW, charge_space / CHARGE_EFF)
                soc += to_charge * CHARGE_EFF
                export = surplus - to_charge
            else:
                export = surplus
                
        elif deficit > 0:
            if capacity_kwh > 0:
                from_battery = min(deficit, MAX_DISCHARGE_KW, soc * DISCHARGE_EFF)
                soc -= from_battery / DISCHARGE_EFF
                grid_import = deficit - from_battery
            else:
                grid_import = deficit
        
        res.append({
            "export_kWh": export,
            "import_kWh": grid_import,
            "soc_kwh": soc,
            "direct_use": direct_use
        })
        
    df_res = pd.concat([df.reset_index(), pd.DataFrame(res)], axis=1)
    df_res["import_cost"] = df_res["import_kWh"] * import_price
    df_res["export_gain"] = df_res["export_kWh"] * EXPORT_PRICE
    df_res["net_cost"] = df_res["import_cost"] - df_res["export_gain"]
    
    return df_res

def simulate_multi_year(base_df, nominal_capacity_kwh, scenario_name, years=YEARS):
    annual_results = []
    
    # Počiatočná investícia
    initial_investment = FVE_FIXED_COST + (nominal_capacity_kwh * BATTERY_PRICE_PER_KWH)
    cumulative_cashflow = -initial_investment
    payback_year = np.nan

    for year in range(1, years + 1):
        
        # --- Riešenie starnutia a REINVESTÍCIE ---
        capex_this_year = 0
        
        if year == REINVESTMENT_YEAR:
            # V 15. roku kupujeme nový striedač a novú batériu 
            capex_this_year += INVERTER_COST
            if nominal_capacity_kwh > 0:
                capex_this_year += nominal_capacity_kwh * BATTERY_REPLACEMENT_PRICE_PER_KWH
            
            years_since_new_battery = 0 # Batéria je nová, reset degradácie
        elif year > REINVESTMENT_YEAR:
            years_since_new_battery = year - REINVESTMENT_YEAR # Degradácia druhej batérie
        else:
            years_since_new_battery = year - 1 # Degradácia prvej batérie

        # Aktuálna kapacita a výroba po degradácii
        pv_factor = (1 - PV_DEGRADATION_RATE) ** (year - 1)
        bat_cap = nominal_capacity_kwh * ((1 - BATTERY_DEGRADATION_RATE) ** years_since_new_battery)
        
        # Určenie ceny nákupu podľa scenára
        if scenario_name == "constant": curr_import_price = GRID_IMPORT_PRICE
        elif scenario_name == "increase": curr_import_price = GRID_IMPORT_PRICE * ((1 + PRICE_CHANGE_RATE) ** (year - 1))
        else: curr_import_price = GRID_IMPORT_PRICE * ((1 - PRICE_CHANGE_RATE) ** (year - 1))

        # --- EKONOMIKA ---
        # Dom bez ničoho (nakupuje všetko zo siete)
        cost_no_fve = base_df["cons_kWh"].sum() * curr_import_price
        
        # Stav s FVE a batériou
        df_year = base_df.copy()
        df_year["gen_kWh"] *= pv_factor
        sim_res = simulate_battery(df_year, bat_cap, curr_import_price)
        cost_with_fve = sim_res["net_cost"].sum()
        
        annual_saving = cost_no_fve - cost_with_fve
        
        # Hotovostný tok v danom roku (Úspora - prípadná nová investícia)
        cumulative_cashflow += annual_saving - capex_this_year
        
        # Hľadáme rok návratnosti (kedy sme prvýkrát nad nulou)
        if np.isnan(payback_year) and cumulative_cashflow >= 0:
            payback_year = year

        annual_results.append({
            "year": year,
            "capacity_eff_kWh": bat_cap,
            "import_price_eur": curr_import_price,
            "annual_saving_eur": annual_saving,
            "reinvestment_capex_eur": capex_this_year,
            "cumulative_cashflow_eur": cumulative_cashflow,
            "self_sufficiency_pct": (df_year["cons_kWh"].sum() - sim_res["import_kWh"].sum()) / df_year["cons_kWh"].sum() * 100
        })

    annual_df = pd.DataFrame(annual_results)
    
    # Vypočítame si sumy, aby bol vzorec pre ROI prehľadný
    total_savings = annual_df["annual_saving_eur"].sum()
    total_capex = initial_investment + annual_df["reinvestment_capex_eur"].sum()

    summary = {
        "scenario": scenario_name,
        "capacity_kWh": nominal_capacity_kwh,
        "initial_investment": initial_investment,
        "total_capex_25y": total_capex,
        "payback_year": payback_year if not np.isnan(payback_year) else "Nenávratné",
        "total_savings_25y": total_savings,
        "profit_after_25y": cumulative_cashflow,
        "roi_25y_pct": (total_savings / total_capex) * 100,  # <--- TENTO RIADOK
        "avg_self_sufficiency_pct": annual_df["self_sufficiency_pct"].mean()
    }
    
    return summary, annual_df

def main():
    base_df = prepare_base_df()
    scenarios = ["constant", "increase", "decrease"]
    
    all_summaries = []
    
    for scen in scenarios:
        log(f"Simulujem scenár: {scen}")
        for cap in progress_iter(CAPACITY_RANGE, desc=f"Kapacity {scen}"):
            summary, _ = simulate_multi_year(base_df, cap, scen)
            all_summaries.append(summary)

    res_df = pd.DataFrame(all_summaries)
    
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    res_df.to_excel(output_dir / "fve_analyza_navratnosti_25r_11kwp_1pct_14k_dotacia.xlsx", index=False)
    log(f"Hotovo. Výsledky uložené v {output_dir}")

if __name__ == "__main__":
    main()