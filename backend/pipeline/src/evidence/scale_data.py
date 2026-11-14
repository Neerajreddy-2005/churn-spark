"""Synthetic scale dataset, resampled from the 7,043 real rows. For benchmarks only.

Every output row is a uniformly random real row (seeded, so the file is reproducible) with:
- a new unique customerID "SYN00000001", ... (marks the row as synthetic and keeps dq04 unique);
- MonthlyCharges and TotalCharges multiplied by the same factor drawn from U(0.98, 1.02), rounded
  to 2 decimals (a blank TotalCharges stays blank: the dq10 tenure-0 case is preserved).
All other columns, including tenure and Churn, are copied unchanged, so every DQ rule still holds
and reconciliation stays exact. Claims must say "synthetic, resampled from 7,043 real rows".

    python -m src.evidence.scale_data --rows 1000000 --out C:/Users/<you>/churn-spark-scale/1m/raw/Telco-Customer-Churn.csv
"""
import argparse
import csv
import random
import time
from pathlib import Path

from src.config.settings import RAW_CSV

SEED = 20261114
NOISE = 0.02  # +-2% on the two money columns


def _scaled(value, factor):
    return f"{float(value) * factor:.2f}"


def generate(rows, out_path, source=RAW_CSV, seed=SEED):
    with open(source, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        real = list(reader)
    rng = random.Random(seed)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for i in range(1, rows + 1):
            row = dict(rng.choice(real))
            factor = 1 + rng.uniform(-NOISE, NOISE)
            row["customerID"] = f"SYN{i:08d}"
            row["MonthlyCharges"] = _scaled(row["MonthlyCharges"], factor)
            if row["TotalCharges"].strip():
                row["TotalCharges"] = _scaled(row["TotalCharges"], factor)
            writer.writerow(row)
    return {"rows": rows, "source_rows": len(real), "seed": seed, "noise": f"money x U(1-{NOISE}, 1+{NOISE})",
            "bytes": out_path.stat().st_size, "seconds": round(time.perf_counter() - start, 1),
            "path": str(out_path)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    print(generate(args.rows, args.out))
