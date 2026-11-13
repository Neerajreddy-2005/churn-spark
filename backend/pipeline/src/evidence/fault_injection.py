import argparse
import csv
import json
import logging
import random
import tempfile
import time
from datetime import datetime
from pathlib import Path

from src.config.settings import EVIDENCE_DIR, RAW_CSV
from src.ingestion.bronze_loader import load_bronze
from src.ingestion.spark_session import get_spark
from src.output.evidence import write_evidence
from src.quality.runner import DataQualityError, run_dq

logger = logging.getLogger(__name__)

EVIDENCE_NAME = "fault_injection.json"


def _blank_total(row):
    return row["TotalCharges"].strip() == ""


def _any(row):
    return True


# Each defect breaks exactly one DQ rule. Rows are only eligible if the change cannot trip another rule:
#  - dq03 uses whitespace of a different length per row: not null (dq14) and not a duplicate (dq04).
#  - dq06a needs PhoneService = Yes (dq13 only looks at PhoneService = No); dq06c needs internet (dq12).
#  - dq08 needs a non-blank TotalCharges (dq10/dq11 only look at blank TotalCharges).
#  - dq14 uses TotalCharges: a null there is skipped by dq10, dq11 and dq11b (null comparisons are not true).
# (rule id, column, new value for the j-th injected row, eligibility test, number of rows)
DEFECTS = [
    ("dq03", "customerID", lambda j, bad: " " * (j + 1), _any),
    ("dq05a", "gender", lambda j, bad: "Unknown", _any),
    ("dq05b", "Partner", lambda j, bad: "Maybe", _any),
    ("dq06a", "MultipleLines", lambda j, bad: "Sometimes", lambda r: r["PhoneService"] == "Yes"),
    ("dq06b", "InternetService", lambda j, bad: "Satellite", _any),
    ("dq06c", "OnlineSecurity", lambda j, bad: "Partial", lambda r: r["InternetService"] != "No"),
    ("dq06d", "Contract", lambda j, bad: "Weekly", _any),
    ("dq06e", "PaymentMethod", lambda j, bad: "Cash", _any),
    ("dq07", "SeniorCitizen", lambda j, bad: "2", _any),
    ("dq08", "tenure", lambda j, bad: "abc" if j < bad else "-5", _any),
    ("dq09", "MonthlyCharges", lambda j, bad: "n/a" if j < bad else "-10.00", _any),
    ("dq11", "TotalCharges", lambda j, bad: " ", lambda r: r["tenure"] != "0"),
    ("dq11b", "TotalCharges", lambda j, bad: "12,34x", _any),
    ("dq12", "StreamingTV", lambda j, bad: "Yes", lambda r: r["InternetService"] == "No"),
    ("dq13", "MultipleLines", lambda j, bad: "Yes", lambda r: r["PhoneService"] == "No"),
    ("dq14", "TotalCharges", lambda j, bad: "", _any),
]


def inject_faults(src_csv, out_csv, n_per_rule=25, seed=42):
    """Copy src_csv to out_csv with n_per_rule defects per rule on distinct rows. Returns what to expect."""
    with open(src_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header, rows = reader.fieldnames, list(reader)

    # Untouched rules keep their real-data baseline: 0, except the blank TotalCharges rows (dq10, dq11).
    expected = {
        "dq10": sum(1 for r in rows if _blank_total(r) and r["tenure"] == "0"),
        "dq11": sum(1 for r in rows if _blank_total(r) and r["tenure"] != "0"),
    }
    rng = random.Random(seed)
    # Blank-TotalCharges rows are never touched: they are the real-data baseline for dq10.
    available = [i for i, r in enumerate(rows) if not _blank_total(r)]
    taken = {}  # rule id -> row indexes

    def pick(eligible, n):
        pool = [i for i in available if eligible(rows[i])]
        if len(pool) < n:
            raise ValueError(f"Only {len(pool)} eligible rows, need {n}")
        chosen = rng.sample(pool, n)
        chosen_set = set(chosen)
        available[:] = [i for i in available if i not in chosen_set]
        return chosen

    # dq04: k rows take the id of k other untouched rows, so 2k rows share an id.
    both = pick(_any, 2 * n_per_rule)
    targets, sources = both[:n_per_rule], both[n_per_rule:]
    for t, s in zip(targets, sources):
        rows[t]["customerID"] = rows[s]["customerID"]
    taken["dq04"] = targets

    bad = max(1, n_per_rule // 5)  # a few non-numeric values among the out-of-range ones
    for rule_id, column, new_value, eligible in DEFECTS:
        chosen = pick(eligible, n_per_rule)
        for j, i in enumerate(chosen):
            rows[i][column] = new_value(j, bad)
        taken[rule_id] = chosen

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

    for rule_id, idx in taken.items():
        expected[rule_id] = expected.get(rule_id, 0) + (2 * len(idx) if rule_id == "dq04" else len(idx))
    return {
        "expected": expected,
        "injected": {rule_id: len(idx) for rule_id, idx in taken.items()},
        "rows": {rule_id: sorted(idx) for rule_id, idx in taken.items()},
        "targeted": sorted(taken),
    }


def compare(results, plan):
    table = []
    for r in results:
        rule_id = r["rule_id"]
        expected = plan["expected"].get(rule_id, 0)
        detected = r["failing_rows"]
        table.append({
            "rule_id": rule_id,
            "targeted": rule_id in plan["targeted"],
            "injected": plan["injected"].get(rule_id, 0),
            "expected": expected,
            "detected": detected,
            "detection_rate": round(detected / expected, 4) if expected else None,
            "exact_match": detected == expected,
        })
    return table


def run_fault_injection(spark, n_per_rule=25, seed=42, src_csv=RAW_CSV, evidence_dir=EVIDENCE_DIR):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        plan = inject_faults(src_csv, tmp / "faulty.csv", n_per_rule, seed)
        load_bronze(spark, tmp / "faulty.csv", bronze_path=tmp / "bronze")
        blocked = False
        try:
            run_dq(spark, bronze_path=tmp / "bronze", results_path=tmp / "dq_results",
                   report_json=tmp / "dq_report.json")
        except DataQualityError as e:
            blocked = True
            logger.info("Pipeline blocked as expected: %s", e)
        results = json.loads((tmp / "dq_report.json").read_text(encoding="utf-8"))["results"]

    table = compare(results, plan)
    targeted = [t for t in table if t["targeted"]]
    report = {
        "run_timestamp": datetime.now().astimezone().isoformat(),
        "seed": seed,
        "n_per_rule": n_per_rule,
        "pipeline_blocked": blocked,
        "rules": table,
        "overall": {
            "rules_checked": len(table),
            "rules_exact_match": sum(t["exact_match"] for t in table),
            "rules_targeted": len(targeted),
            "targeted_exact_match": sum(t["exact_match"] for t in targeted),
            "total_injected": sum(t["injected"] for t in targeted),
            "total_expected_failing_rows": sum(t["expected"] for t in targeted),
            "total_detected_failing_rows": sum(t["detected"] for t in targeted),
        },
    }
    write_evidence(EVIDENCE_NAME, report, evidence_dir=evidence_dir)
    return report


def format_table(report):
    lines = [f"{'rule':<7}{'target':<8}{'injected':>9}{'expected':>9}{'detected':>9}{'rate':>8}  exact"]
    for t in report["rules"]:
        rate = "-" if t["detection_rate"] is None else f"{t['detection_rate']:.0%}"
        lines.append(f"{t['rule_id']:<7}{'yes' if t['targeted'] else 'no':<8}{t['injected']:>9}"
                     f"{t['expected']:>9}{t['detected']:>9}{rate:>8}  {'yes' if t['exact_match'] else 'NO'}")
    o = report["overall"]
    lines.append(f"Pipeline blocked: {report['pipeline_blocked']}")
    lines.append(f"Rules exact match: {o['rules_exact_match']}/{o['rules_checked']} "
                 f"(targeted {o['targeted_exact_match']}/{o['rules_targeted']})")
    lines.append(f"Injected defects: {o['total_injected']}; expected failing rows: "
                 f"{o['total_expected_failing_rows']}; detected: {o['total_detected_failing_rows']}")
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    start = time.perf_counter()
    spark = get_spark()
    try:
        report = run_fault_injection(spark, args.n, args.seed)
    finally:
        spark.stop()
    print(format_table(report))
    print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
