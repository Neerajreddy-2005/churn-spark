import csv
from decimal import Decimal

# Deliberately independent of Spark and src.transformations: a bug there must not hide itself here.
SEGMENT_COLUMNS = {
    "internet": "InternetService",
    "contract": "Contract",
    "payment": "PaymentMethod",
}


def tenure_band(tenure):
    t = int(tenure)
    if t <= 12:
        return "0-12"
    if t <= 24:
        return "13-24"
    if t <= 48:
        return "25-48"
    return "49-72"


def compute_source_stats(csv_path):
    ids = set()
    row_count = churned = blank = 0
    total = Decimal(0)
    segments = {name: {} for name in [*SEGMENT_COLUMNS, "tenure"]}

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row_count += 1
            ids.add(row["customerID"])
            is_churn = row["Churn"] == "Yes"
            churned += is_churn
            charges = row["TotalCharges"].strip()
            if charges == "":
                blank += 1
            else:
                total += Decimal(charges)

            keys = {name: row[col] for name, col in SEGMENT_COLUMNS.items()}
            keys["tenure"] = tenure_band(row["tenure"])
            for name, key in keys.items():
                seg = segments[name].setdefault(key, {"customers": 0, "churned": 0})
                seg["customers"] += 1
                seg["churned"] += is_churn

    return {
        "row_count": row_count,
        "distinct_ids": len(ids),
        "churned": churned,
        "blank_total_charges": blank,
        "total_charges_sum": total.quantize(Decimal("0.01")),
        "segments": segments,
    }
