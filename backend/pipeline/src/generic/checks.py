"""General data checks for a reviewed dataset (the Mode 2 counterpart of the 20 Telco rules).

Input: the stored profile and the review choices. Pure Python, so the outcome is decided before any
training starts and is fully unit-testable.
"""
MIN_ROWS = 200
MIN_MINORITY = 30
MAX_FEATURES = 99

# Any failure means no model: the dataset keeps its exploration and the failing rules are shown.
RULES = {
    "g01": "ID column filled on every row",
    "g02": "ID column unique",
    "g03": "Target has exactly two values",
    "g04": f"At least {MIN_ROWS} rows to train",
    "g05": f"At least {MIN_MINORITY} customers in the smaller target group",
    "g06": "At least one usable feature column",
    "g07": "Target, ID and features are different columns",
}


def _result(rule_id, passed, failing, detail):
    return {"rule_id": rule_id, "description": RULES[rule_id], "status": "pass" if passed else "fail",
            "failing_rows": failing, "detail": detail}


def run_checks(profile, review):
    """review: {"id": key|None, "target": key, "positive": value, "features": [keys]}."""
    rows = profile["rows"]
    cols = {c["key"]: c for c in profile["columns"]}
    out = []

    id_key = review.get("id")
    if id_key is None:  # row number is used: always filled and unique
        out += [_result("g01", True, 0, "Row number is used as the ID."),
                _result("g02", True, 0, "Row number is used as the ID.")]
    else:
        c = cols[id_key]
        out.append(_result("g01", c["blank"] == 0, c["blank"], f"{c['blank']:,} blank IDs."))
        dup = c["filled"] - c["distinct"]
        out.append(_result("g02", dup == 0, dup, f"{dup:,} rows repeat an ID already used."))

    t = cols[review["target"]]
    values = {v: n for v, n in t["top"]}
    out.append(_result("g03", t["distinct"] == 2 and t["blank"] == 0 and review.get("positive") in values,
                       t["blank"], f"{t['distinct']} distinct values, {t['blank']:,} blank."))
    out.append(_result("g04", rows >= MIN_ROWS, 0, f"{rows:,} rows."))
    smaller = min(values.values()) if len(values) == 2 else 0
    out.append(_result("g05", smaller >= MIN_MINORITY, 0, f"Smaller group: {smaller:,} customers."))
    feats = review.get("features") or []
    out.append(_result("g06", 0 < len(feats) <= MAX_FEATURES, 0, f"{len(feats)} feature columns."))
    keys = [k for k in [id_key, review["target"], *feats] if k is not None]
    out.append(_result("g07", len(keys) == len(set(keys)), 0, "No column is used twice."))
    return out


def can_train(results):
    return all(r["status"] == "pass" for r in results)
