import json
import os
import random
import matplotlib.pyplot as plt
import numpy as np

INPUT_DIR = "json2"
OUTPUT_DIR = "grafy2"
DAYS_TO_SAMPLE = 10   # koľko náhodných dní z každého súboru

os.makedirs(OUTPUT_DIR, exist_ok=True)

for filename in os.listdir(INPUT_DIR):
    if not filename.endswith(".json"):
        continue

    filepath = os.path.join(INPUT_DIR, filename)

    with open(filepath, "r") as f:
        days = json.load(f)

    sample_days = random.sample(days, DAYS_TO_SAMPLE)

    for day in sample_days:
        consumption = day["consumption"]
        year = day["year"]
        month = day["month"]
        d = day["day"]

        n = len(consumption)

        # časová os v hodinách (0–24)
        time_hours = np.linspace(0, 24, n)

        plt.figure(figsize=(12, 4))
        plt.plot(time_hours, consumption)

        # ---- X os: hodiny ----
        plt.xticks(
            ticks=[0, 4, 8, 12, 16, 20, 24],
            labels=["0:00", "4:00", "8:00", "12:00", "16:00", "20:00", "24:00"]
        )

        plt.title(f"Consumption – {year}-{month:02d}-{d:02d}")
        plt.xlabel("Čas")
        plt.ylabel("kWh")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()

        out_name = f"{filename[:-5]}_{year}-{month:02d}-{d:02d}.png"
        plt.savefig(os.path.join(OUTPUT_DIR, out_name))
        plt.close()

print("HOTOVO!")
