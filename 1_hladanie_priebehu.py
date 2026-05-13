import json
import os
import numpy as np
import csv

INPUT_DIR = "."
OUTPUT_DIR = "output_selection"
TOP_N = 10

os.makedirs(OUTPUT_DIR, exist_ok=True)

results = []

def daily_to_hourly(cons_15min):
    arr = np.array(cons_15min)
    if len(arr) != 96:
        return None
    return arr.reshape(24,4).mean(axis=1)

for filename in os.listdir(INPUT_DIR):
    if not filename.endswith(".json"):
        continue

    with open(filename) as f:
        days = json.load(f)

    hourly_days = []

    for day in days:
        cons = day.get("consumption")
        if not cons:
            continue
        h = daily_to_hourly(cons)
        if h is not None:
            hourly_days.append(h)

    if len(hourly_days) < 200:
        continue

    avg_day = np.mean(hourly_days, axis=0)

    night = avg_day[0:6].mean()
    morning = avg_day[6:10].mean()
    day = avg_day[10:17].mean()
    evening = avg_day[17:23].mean()

    # základné ukazovatele
    if day < 0.3:       # takmer nula cez deň → nereálne
        continue
    if night > evening: # noc vyššia než večer → podozrivé
        continue

    # variabilita profilu 
    std = np.std(avg_day)

    # penalizácia plochého profilu
    flat_penalty = 0
    if std < 0.1:
        flat_penalty = 1.0

    # skóre
    score = (
        (evening - day) * 1.5 +
        (morning - night) * 1.0 -
        abs(day - night) * 0.5 +
        std * 0.8 -
        flat_penalty
    )

    results.append({
        "file": filename,
        "score": float(score),
        "night": float(night),
        "morning": float(morning),
        "day": float(day),
        "evening": float(evening),
        "std": float(std)
    })

results.sort(key=lambda x: x["score"], reverse=True)

with open(os.path.join(OUTPUT_DIR, "household_ranking_realistic.csv"), "w", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["file","score","night","morning","day","evening","std"]
    )
    writer.writeheader()
    writer.writerows(results)

print("TOP realistické domácnosti:")
for r in results[:TOP_N]:
    print(r["file"], "score:", round(r["score"],3))
