# kombinacia.py
"""
Kombinácia výroby (PVGIS) a spotreby (15-min / 30-min / 60-min meter data).
Výstup:
 - hourly_balance_meter_XX.csv
 - hourly_balance_meter_XX.html
"""

import json
import pandas as pd
import numpy as np
import plotly.express as px

# ----------------- SETTINGS (uprav len tieto riadky) -----------------

pv_file_1 = "data_13.json"         # prvý PVGIS JSON
pv_file_2 = "data_90.json"         # druhý PVGIS JSON
meters_file = "meters_731_measurement.json"  # spotreba
meter_id = "731"

output_csv = f"hourly_balance_meter_{meter_id}.csv"
output_html = f"hourly_balance_meter_{meter_id}.html"

# --------------------------------------------------------------------

def load_pv(pv_path):
    with open(pv_path, "r") as f:
        data = json.load(f)
    df = pd.DataFrame(data["outputs"]["hourly"])
    
    df["datetime"] = pd.to_datetime(df["time"], format="%Y%m%d:%H%M")
    return df.set_index("datetime")

def build_meter_ts(meters_path, meter_id):
    with open(meters_path, "r") as f:
        meters = json.load(f)

    entries = [m for m in meters if str(m.get("meterID")) == str(meter_id)]
    if not entries:
        raise ValueError(f"No meterID {meter_id} in {meters_path}")

    # Preskočiť posledných 30 dní merania ak je ich viac ako 30
    if len(entries) > 30:
        entries = entries[:-30]

    rows = []

    for e in entries:
        year = int(e["year"])
        month = int(e["month"])
        day = int(e["day"])
        cons_list = e.get("consumption", [])
        n = len(cons_list)

        valid_lengths = [100, 96, 92, 48, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
        if n not in valid_lengths:
            raise ValueError(f"Nepodporovaný počet záznamov: {n}")

        if n in (100, 96, 92):
            interval_min = 15
        elif n == 48:
            interval_min = 30
        elif n == 24:
            interval_min = 60
        elif n < 24:
            print(f"Skipping incomplete day {year}-{month}-{day} with only {n} records.")

        base = pd.Timestamp(year=year, month=month, day=day, hour=0, minute=0)

        for i, value in enumerate(cons_list):
            ts = base + pd.Timedelta(minutes=i * interval_min)
            rows.append({
                "datetime": ts,
                "cons_kWh_interval": float(value)
            })

    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    return df

def main():
    # Načítanie PV
    pv1 = load_pv(pv_file_1)[["P"]].rename(columns={"P": "P_m13"})
    pv2 = load_pv(pv_file_2)[["P"]].rename(columns={"P": "P_m90"})

    # Zaokrúhli čas na celé hodiny, aby sedel so spotrebou
    pv1.index = pv1.index.floor('H')
    pv2.index = pv2.index.floor('H')

    pv = pv1.join(pv2, how="inner").sort_index()
    pv["P_combined_W"] = pv["P_m13"].fillna(0.0) + pv["P_m90"].fillna(0.0)
    pv["gen_kWh"] = pv["P_combined_W"] / 1000.0

    # Načítanie spotreby
    meter_ts = build_meter_ts(meters_file, meter_id)

    # Agregácia spotreby na hodinové kWh
    meter_hourly = meter_ts["cons_kWh_interval"].resample("H").sum().to_frame("cons_kWh")

    # Spojenie podľa datetime, iba spoločné dátumy/hodiny
    df = pv[["gen_kWh"]].join(meter_hourly, how="inner")

    if df.empty:
        print("Warning: No overlapping timestamps found. Skipping export, žiadne spoločné dni!")
        return

    # Výpočty
    df["used_kWh"] = np.minimum(df["gen_kWh"], df["cons_kWh"])
    df["exported_kWh"] = np.maximum(0.0, df["gen_kWh"] - df["cons_kWh"])
    df["unmet_kWh"] = np.maximum(0.0, df["cons_kWh"] - df["gen_kWh"])

    # Uloženie CSV
    df.reset_index().to_csv(output_csv, index=False)

    # Súhrn
    print("---- SUMMARY ----")
    print("Period:", df.index.min(), "→", df.index.max())
    print("Total generation [kWh]:", df["gen_kWh"].sum())
    print("Total consumption [kWh]:", df["cons_kWh"].sum())
    print("Used from PV [kWh]:", df["used_kWh"].sum())
    print("Exported [kWh]:", df["exported_kWh"].sum())
    print("Self-consumption [%]:", 100 * df["used_kWh"].sum() / df["gen_kWh"].sum())

    # Interaktívny graf
    plot_df = df.reset_index().melt(
        id_vars="datetime",
        value_vars=["gen_kWh", "cons_kWh", "used_kWh", "exported_kWh"],
        var_name="variable",
        value_name="kWh"
    )

    fig = px.line(plot_df, x="datetime", y="kWh", color="variable",
                  title=f"Hourly Balance – Meter {meter_id}")
    fig.update_layout(yaxis_title="kWh per hour")
    fig.write_html(output_html)

    print("\nGenerated files:")
    print(" →", output_csv)
    print(" →", output_html)

if __name__ == "__main__":
    main()
