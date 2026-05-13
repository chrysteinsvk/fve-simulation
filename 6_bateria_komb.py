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
FVE_FIXED_COST = 10447        # € (Panely, inštalácia, striedač MAP0)
BATTERY_COST = 4920           # € (Huawei LUNA2000 6,9 kWh + DC/DC BMS modul)
VB_MONTHLY_FEE = 3.00         # € (Mesačný poplatok za Virtuálnu batériu ZSE)

# --- REINVESTÍCIA ---
REINVESTMENT_YEAR = 15
INVERTER_COST = 1500          # € (Nový striedač v 15. roku)


# ===================== PARAMETRE BATÉRIE (Huawei 10 kWh) =====================
BATTERY_CAPACITY_KWH = 10
MAX_CHARGE_KW = 5           
MAX_DISCHARGE_KW = 5
CHARGE_EFF = 0.95
DISCHARGE_EFF = 0.95
BATTERY_DEGRADATION_RATE = 0.015 

# ===================== OSTATNÉ NASTAVENIA =====================
pv_file_1 = "data_13.json"
pv_file_2 = "data_90.json"
meters_file = "meters_731_measurement.json"
meter_id = "731"

YEARS = 25                    
PRICE_CHANGE_RATE = 0.01      
PV_DEGRADATION_RATE = 0.005   
GRID_IMPORT_PRICE = 0.18      

# ===============================================================

def progress_iter(iterable, total=None, desc=None):
    if tqdm is not None: return tqdm(iterable, total=total, desc=desc)
    return iterable

def load_pv(path):
    with open(path, encoding="utf-8") as f: data = json.load(f)
    df = pd.DataFrame(data["outputs"]["hourly"])
    df["datetime"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M")
    df["P"] = pd.to_numeric(df["P"], errors="coerce")
    return df.set_index("datetime").sort_index()

def build_meter_ts(path, meter_id):
    with open(path, encoding="utf-8") as f: meters = json.load(f)
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
    pv1 = load_pv(pv_file_1)[["P"]].rename(columns={"P": "pv1"})
    pv2 = load_pv(pv_file_2)[["P"]].rename(columns={"P": "pv2"})
    pv1.index = pv1.index.floor("h")
    pv2.index = pv2.index.floor("h")
    pv = pv1.join(pv2, how="inner")
    pv["gen_kWh"] = (pv["pv1"] + pv["pv2"]) / 1000.0
    meter = build_meter_ts(meters_file, meter_id)
    cons_h = meter["cons"].resample("h").sum()
    df = pv.join(cons_h, how="inner").rename(columns={"cons": "cons_kWh"}).dropna()
    df['hour'] = df.index.hour
    df['is_weekend'] = df.index.dayofweek >= 5 
    return df

def get_zse_export_price(row):
    if row['is_weekend']: return 0.04879 
    h = row['hour']
    if 0 <= h <= 9: return 0.08211 
    elif 10 <= h <= 17: return 0.04760 
    else: return 0.08092 

def simulate_hybrid_system(df, cap_kwh, import_price):
    soc = 0.0
    res = []
    
    for r in df.itertuples():
        gen, cons = r.gen_kWh, r.cons_kWh
        
        # 1. Priorita: Priama spotreba
        direct_use = min(gen, cons)
        surplus = gen - direct_use
        deficit = cons - direct_use
        
        export = 0.0
        grid_import = 0.0
        
        # 2. Priorita: Fyzická batéria (Nabíjanie prebytkov)
        if surplus > 0:
            charge_space = cap_kwh - soc
            to_charge = min(surplus, MAX_CHARGE_KW, charge_space / CHARGE_EFF)
            soc += to_charge * CHARGE_EFF
            export = surplus - to_charge # Zvyšok letí do VB (ZSE)
            
        # 2. Priorita v noci: Fyzická batéria (Vybíjanie)
        elif deficit > 0:
            from_battery = min(deficit, MAX_DISCHARGE_KW, soc * DISCHARGE_EFF)
            soc -= from_battery / DISCHARGE_EFF
            grid_import = deficit - from_battery # Zvyšok sa kúpi zo siete
        
        res.append({
            "direct_use": direct_use,
            "export_kWh": export,
            "import_kWh": grid_import,
            "soc_kwh": soc
        })
        
    df_res = pd.concat([df.reset_index(), pd.DataFrame(res)], axis=1)
    df_res["import_cost"] = df_res["import_kWh"] * import_price
    df_res["export_credit"] = df_res["export_kWh"] * df_res["zse_export_price"]
    
    return df_res

def run_multi_year(base_df, scenario_name, years=YEARS):
    annual_results = []
    initial_investment = FVE_FIXED_COST + BATTERY_COST
    cumulative_cashflow = -initial_investment
    payback_year = np.nan
    base_df['zse_export_price'] = base_df.apply(get_zse_export_price, axis=1)

    for year in range(1, years + 1):
        capex_this_year = 0
        if year == REINVESTMENT_YEAR:
            # Vymeníme striedač a kúpime nový batériový modul 
            capex_this_year = INVERTER_COST + 2000 
            
        vb_annual_fee = VB_MONTHLY_FEE * 12 
        
        pv_factor = (1 - PV_DEGRADATION_RATE) ** (year - 1)
        bat_cap = BATTERY_CAPACITY_KWH * ((1 - BATTERY_DEGRADATION_RATE) ** (year - 1))
        
        if scenario_name == "constant": curr_import_price = GRID_IMPORT_PRICE
        elif scenario_name == "increase": curr_import_price = GRID_IMPORT_PRICE * ((1 + PRICE_CHANGE_RATE) ** (year - 1))
        else: curr_import_price = GRID_IMPORT_PRICE * ((1 - PRICE_CHANGE_RATE) ** (year - 1))

        df_year = base_df.copy()
        df_year["gen_kWh"] *= pv_factor
        
        sim_res = simulate_hybrid_system(df_year, bat_cap, curr_import_price)
        
        cost_no_fve = df_year["cons_kWh"].sum() * curr_import_price
        net_electricity_bill = sim_res["import_cost"].sum() - sim_res["export_credit"].sum() + vb_annual_fee
        
        annual_saving = cost_no_fve - net_electricity_bill
        cumulative_cashflow += annual_saving - capex_this_year
        
        if np.isnan(payback_year) and cumulative_cashflow >= 0:
            payback_year = year

        annual_results.append({
            "year": year,
            "import_price_eur": curr_import_price,
            "annual_saving_eur": annual_saving,
            "total_export_credit_eur": sim_res["export_credit"].sum(),
            "reinvestment_capex_eur": capex_this_year,
            "cumulative_cashflow_eur": cumulative_cashflow,
            "self_sufficiency_pct": ((sim_res["direct_use"].sum() + (df_year["cons_kWh"].sum() - sim_res["import_kWh"].sum() - sim_res["direct_use"].sum())) / df_year["cons_kWh"].sum()) * 100
        })

    annual_df = pd.DataFrame(annual_results)
    total_savings = annual_df["annual_saving_eur"].sum()
    total_capex = initial_investment + annual_df["reinvestment_capex_eur"].sum()

    summary = {
        "scenario": scenario_name,
        "type": "Hybrid (6.9kWh + ZSE)",
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
    print("Spúšťam Hybridný model (Fyzická batéria + Virtuálna)...", flush=True)
    base_df = prepare_base_df()
    scenarios = ["constant", "increase", "decrease"]
    
    all_summaries, all_annuals = [], []
    
    for scen in scenarios:
        summary, annual_df = run_multi_year(base_df, scen)
        annual_df.insert(0, 'scenario', scen) 
        all_summaries.append(summary)
        all_annuals.append(annual_df)

    res_df = pd.DataFrame(all_summaries)
    annual_res_df = pd.concat(all_annuals, ignore_index=True)
    
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "fve_analyza_hybrid_6_9kwh_11kwp_1pct_14k_dotacia.xlsx"
    
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        res_df.to_excel(writer, sheet_name="summary", index=False)
        annual_res_df.to_excel(writer, sheet_name="annual_results", index=False)
        
    print(f"Hotovo. Výsledky uložené v {output_file}", flush=True)

if __name__ == "__main__":
    main()