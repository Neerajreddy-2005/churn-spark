import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from pyspark.sql import functions as F

from src.config.storage import read_delta
from src.config.settings import (
    BRONZE_PATH, DQ_REPORT_JSON, GOLD_DIR, RAW_CSV, RECON_REPORT_JSON, RECON_RESULTS_PATH,
    SILVER_PATH,
)
from src.ingestion.spark_session import get_spark
from src.reconciliation.report import write_results
from src.reconciliation.source_calc import compute_source_stats

logger = logging.getLogger(__name__)

SEGMENT_GOLD = {
    "internet": "gold_churn_by_internet_service",
    "contract": "gold_churn_by_contract",
    "payment": "gold_churn_by_payment_method",
    "tenure": "gold_churn_by_tenure_band",
}
GOLD_TABLES = {"overall": "gold_churn_overall", **SEGMENT_GOLD}


class ReconciliationError(Exception):
    pass


def _rate_ok(row):
    """churn_rate_pct = round(100*churned/customers, 2); both null when churn is unknown."""
    if row["churned"] is None or row["churn_rate_pct"] is None:
        return row["churned"] is None and row["churn_rate_pct"] is None
    return abs(float(row["churn_rate_pct"]) - float(expected_rate(row["customers"], row["churned"]))) < 1e-9


def expected_rate(customers, churned):
    if customers == 0:
        return Decimal("0.00")
    return (Decimal(100 * churned) / Decimal(customers)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _result(run_id, run_ts, check_id, description, expected, actual, difference, passed):
    return {
        "run_id": run_id, "check_id": check_id, "description": description,
        "expected": str(expected), "actual": str(actual),
        "difference": None if difference is None else float(difference),
        "status": "pass" if passed else "fail", "run_ts": run_ts,
    }


def run_checks(spark, source, bronze_path, silver_path, gold_dir, dq_report_json, run_id):
    run_ts = datetime.now(timezone.utc).isoformat()
    results = []

    def numeric(check_id, description, expected, actual):
        results.append(_result(run_id, run_ts, check_id, description, expected, actual,
                               actual - expected, actual == expected))

    def load(path):
        return read_delta(spark, path)

    bronze = load(bronze_path)
    silver = load(silver_path)
    bronze_count = bronze.count()
    silver_count = silver.count()
    labelled = source["labelled"]
    # Unlabelled file (no Churn column): churn must stay unknown (null) all the way through.
    silver_churned = silver.filter(F.col("churn") if labelled else F.col("churn").isNotNull()).count()

    numeric("rc01", "Source row count = Bronze row count", source["row_count"], bronze_count)
    numeric("rc02", "Bronze row count = Silver row count", bronze_count, silver_count)
    if labelled:
        numeric("rc03", "Silver churned = source churned", source["churned"], silver_churned)
    else:
        numeric("rc03", "Unlabelled file: Silver rows with a churn value", 0, silver_churned)
    numeric("rc04.bronze", "Distinct customer ids: source = Bronze", source["distinct_ids"],
            bronze.select("customerID").distinct().count())
    numeric("rc04.silver", "Distinct customer ids: source = Silver", source["distinct_ids"],
            silver.select("customer_id").distinct().count())

    gold = {short: load(Path(gold_dir) / table).collect() for short, table in GOLD_TABLES.items()}

    for short, rows in gold.items():
        numeric(f"rc05.{short}", f"Gold {short}: sum(customers) = Silver count",
                silver_count, sum(r["customers"] for r in rows))
    for short, rows in gold.items():
        if labelled:
            numeric(f"rc06.{short}", f"Gold {short}: sum(churned) = source churned",
                    source["churned"], sum(r["churned"] for r in rows))
        else:
            numeric(f"rc06.{short}", f"Unlabelled file: Gold {short} rows with a churned value",
                    0, sum(1 for r in rows if r["churned"] is not None))

    for short in SEGMENT_GOLD:
        exp = {k: [v["customers"], v["churned"]] for k, v in source["segments"][short].items()}
        act = {r["segment"]: [r["customers"], r["churned"]] for r in gold[short]}
        mismatches = sum(1 for k in exp.keys() | act.keys() if exp.get(k) != act.get(k))
        results.append(_result(
            run_id, run_ts, f"rc07.{short}", f"Gold {short}: segment counts = source groups",
            json.dumps(exp, sort_keys=True), json.dumps(act, sort_keys=True),
            mismatches, mismatches == 0))

    for short, rows in gold.items():
        bad = sum(1 for r in rows if not _rate_ok(r))
        results.append(_result(
            run_id, run_ts, f"rc08.{short}",
            f"Gold {short}: churn_rate_pct = round(100*churned/customers, 2)",
            len(rows), len(rows) - bad, bad, bad == 0))

    imputed = silver.filter(F.col("total_charges_imputed")).count()
    report = json.loads(Path(dq_report_json).read_text(encoding="utf-8"))
    dq10 = next(r["failing_rows"] for r in report["results"] if r["rule_id"] == "dq10")
    results.append(_result(
        run_id, run_ts, "rc11", "Silver imputed rows = dq10 failing rows = source blank TotalCharges",
        source["blank_total_charges"], f"silver={imputed}, dq10={dq10}",
        imputed - source["blank_total_charges"],
        imputed == dq10 == source["blank_total_charges"]))

    total = silver.agg(F.sum(F.col("total_charges").cast("decimal(18,2)"))).collect()[0][0]
    total = Decimal(total).quantize(Decimal("0.01")) if total is not None else Decimal("0.00")
    numeric("rc12", "Silver sum(total_charges) = source sum", source["total_charges_sum"], total)
    return results


def enforce(results):
    failed = [r["check_id"] for r in results if r["status"] == "fail"]
    if failed:
        raise ReconciliationError(f"Reconciliation failed: {', '.join(failed)}")


def run_reconciliation(spark, csv_path=RAW_CSV, bronze_path=BRONZE_PATH, silver_path=SILVER_PATH,
                       gold_dir=GOLD_DIR, dq_report_json=DQ_REPORT_JSON,
                       results_path=RECON_RESULTS_PATH, report_json=RECON_REPORT_JSON, run_id=None):
    run_id = run_id or uuid.uuid4().hex
    source = compute_source_stats(csv_path)
    results = run_checks(spark, source, bronze_path, silver_path, gold_dir, dq_report_json, run_id)
    write_results(spark, results, results_path, report_json)
    enforce(results)
    return results


def _short(text, limit=60):
    return text if len(text) <= limit else text[:limit - 3] + "..."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start = time.perf_counter()
    spark = get_spark()
    code = 0
    results = []
    try:
        results = run_reconciliation(spark)
    except ReconciliationError as e:
        print(e)
        code = 1
    finally:
        spark.stop()
    for r in results:
        print(f"{r['check_id']:<14}{r['status']:<6}{_short(r['expected'])} | {_short(r['actual'])}")
    print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
    sys.exit(code)
