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
FVE_FIXED_COST = 9748        # € (Panely, inštalácia, striedač)
VB_MONTHLY_FEE = 3.00         # € (Mesačný poplatok za Virtuálnu batériu s DPH)

# --- REINVESTÍCIA ---
REINVESTMENT_YEAR = 15
INVERTER_COST = 1500          # € (Nový striedač v 15. roku. Batériu nekupujeme)

# ===================== OSTATNÉ NASTAVENIA =====================
pv_file_1 = "data_13.json"
pv_file_2 = "data_90.json"
meters_file = "meters_731_measurement.json"
meter_id = "731"

YEARS = 25                    
PRICE_CHANGE_RATE = 0.01      # 1 % ročná zmena ceny (pre nákup elektriny)
PV_DEGRADATION_RATE = 0.005   # 0.5 % pokles výroby panelov ročne
GRID_IMPORT_PRICE = 0.18      # €/kWh (Cena nákupu zo siete)

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
    
    # Príprava časových značiek pre cenové pásma ZSE
    df['hour'] = df.index.hour
    df['is_weekend'] = df.index.dayofweek >= 5 # 5=Sobota, 6=Nedeľa
    
    return df

def get_zse_export_price(row):
    """Priradí výkupnú cenu podľa ZSE pásiem (s DPH)."""
    if row['is_weekend']:
        return 0.04879 # Pásmo 4
    else:
        h = row['hour']
        if 0 <= h <= 9:
            return 0.08211 # Pásmo 1
        elif 10 <= h <= 17:
            return 0.04760 # Pásmo 2
        else:
            return 0.08092 # Pásmo 3

def simulate_virtual_battery(base_df, scenario_name, years=YEARS):
    annual_results = []
    
    initial_investment = FVE_FIXED_COST
    cumulative_cashflow = -initial_investment
    payback_year = np.nan

    # Predpočítanie výkupnej ceny pre každú hodinu
    base_df['zse_export_price'] = base_df.apply(get_zse_export_price, axis=1)

    years_iter = progress_iter(range(1, years + 1), total=years, desc=f"Simulácia VB | {scenario_name}")

    for year in years_iter:
        
        # --- Investície a fixné poplatky ---
        capex_this_year = INVERTER_COST if year == REINVESTMENT_YEAR else 0
        vb_annual_fee = VB_MONTHLY_FEE * 12 # 36 € ročne za vedenie služby
        
        # Vývoj cien a degradácia
        pv_factor = (1 - PV_DEGRADATION_RATE) ** (year - 1)
        
        if scenario_name == "constant": curr_import_price = GRID_IMPORT_PRICE
        elif scenario_name == "increase": curr_import_price = GRID_IMPORT_PRICE * ((1 + PRICE_CHANGE_RATE) ** (year - 1))
        else: curr_import_price = GRID_IMPORT_PRICE * ((1 - PRICE_CHANGE_RATE) ** (year - 1))

        # --- Simulácia tokov (Vektorizovaný výpočet pre rýchlosť) ---
        df_year = base_df.copy()
        df_year["gen_kWh"] *= pv_factor
        
        df_year["direct_use"] = np.minimum(df_year["gen_kWh"], df_year["cons_kWh"])
        df_year["surplus"] = df_year["gen_kWh"] - df_year["direct_use"]
        df_year["deficit"] = df_year["cons_kWh"] - df_year["direct_use"]
        
        # Finančné vyrovnanie (Kompemzácia podľa ZSE)
        df_year["import_cost"] = df_year["deficit"] * curr_import_price
        df_year["export_credit"] = df_year["surplus"] * df_year["zse_export_price"]
        
        total_import_cost = df_year["import_cost"].sum()
        total_export_credit = df_year["export_credit"].sum()
        
        # Účet za elektrinu: Nakúpené - Predané + Fixný poplatok za VB
        net_electricity_bill = total_import_cost - total_export_credit + vb_annual_fee
        
        # Baseline: Dom bez ničoho (nakupuje všetko zo siete)
        cost_no_fve = df_year["cons_kWh"].sum() * curr_import_price
        
        # Reálna úspora vďaka FVE + Virtuálnej batérii
        annual_saving = cost_no_fve - net_electricity_bill
        
        # Cashflow
        cumulative_cashflow += annual_saving - capex_this_year
        
        if np.isnan(payback_year) and cumulative_cashflow >= 0:
            payback_year = year

        annual_results.append({
            "year": year,
            "import_price_eur": curr_import_price,
            "annual_saving_eur": annual_saving,
            "total_export_credit_eur": total_export_credit,
            "vb_fee_eur": vb_annual_fee,
            "reinvestment_capex_eur": capex_this_year,
            "cumulative_cashflow_eur": cumulative_cashflow,
            "self_sufficiency_pct": (df_year["direct_use"].sum() / df_year["cons_kWh"].sum()) * 100
        })

    annual_df = pd.DataFrame(annual_results)
    
    total_savings = annual_df["annual_saving_eur"].sum()
    total_capex = initial_investment + annual_df["reinvestment_capex_eur"].sum()

    summary = {
        "scenario": scenario_name,
        "type": "Virtual Battery ZSE",
        "initial_investment": initial_investment,
        "total_capex_25y": total_capex,
        "payback_year": payback_year if not np.isnan(payback_year) else "Nenávratné",
        "total_savings_25y": total_savings,
        "profit_after_25y": cumulative_cashflow,
        "roi_25y_pct": (total_savings / total_capex) * 100,
        "avg_self_sufficiency_pct": annual_df["self_sufficiency_pct"].mean()
    }
    
    return summary, annual_df

def main():
    base_df = prepare_base_df()
    scenarios = ["constant", "increase", "decrease"]
    
    all_summaries = []
    all_annuals = []
    
    for scen in scenarios:
        log(f"Simulujem scenár: {scen}")
        summary, annual_df = simulate_virtual_battery(base_df, scen)
        
        #  stĺpec so scenárom
        annual_df.insert(0, 'scenario', scen) 
        
        all_summaries.append(summary)
        all_annuals.append(annual_df)

    res_df = pd.DataFrame(all_summaries)
    annual_res_df = pd.concat(all_annuals, ignore_index=True)
    
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "fve_analyza_vb_zse_11kwp_1pct_13k_dotacia.xlsx"
    
    log("Zapisujem Excel súbor...")
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        res_df.to_excel(writer, sheet_name="summary", index=False)
        annual_res_df.to_excel(writer, sheet_name="annual_results", index=False)
        
    log(f"Hotovo. Výsledky uložené v {output_file}")

if __name__ == "__main__":
    main()