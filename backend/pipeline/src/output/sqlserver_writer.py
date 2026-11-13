import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from pyspark.sql import functions as F

from src.config.settings import (
    DQ_RESULTS_PATH, EVIDENCE_DIR, GOLD_DIR, MODEL_SCORES_PATH, RECON_RESULTS_PATH, SILVER_PATH, SNAPSHOT_MONTH,
    SQLSERVER_REPORT_JSON, WAREHOUSE_DIR,
)
from src.config.sqlserver import jdbc_options, jdbc_url
from src.ingestion.spark_session import get_spark
from src.output.evidence import write_evidence
from src.reconciliation.checks import GOLD_TABLES, ReconciliationError
from src.reconciliation.sqlserver_checks import run_and_record

logger = logging.getLogger(__name__)

WAREHOUSE_TABLES = [
    "dim_customer", "dim_service_profile", "dim_date", "fact_customer_snapshot",
    "fact_retention_action",
]
# Casts so the written values match the DDL types (only where the Delta type differs).
CASTS = {
    "fact_customer_snapshot": {"churn_flag": "boolean"},
    **{t: {"churn_rate_pct": "decimal(5,2)", "customers": "int", "churned": "int"}
       for t in GOLD_TABLES.values()},
}
SQLSERVER_CHECK_PREFIXES = ("rc09", "rc10", "rc13", "rc14")


def _latest_run(df, exclude_check_prefixes=()):
    if exclude_check_prefixes:
        pattern = "^(" + "|".join(exclude_check_prefixes) + ")"
        df = df.filter(~F.col("check_id").rlike(pattern))
    latest = df.orderBy(F.col("run_ts").desc()).select("run_id").first()
    return df.filter(F.col("run_id") == latest["run_id"]) if latest else df.limit(0)


def _dq_for_sql(df):
    return _latest_run(df).select(
        "run_id", "rule_id", F.col("columns").alias("column_name"),
        F.col("checked_rows").cast("int"), F.col("failing_rows").cast("int"),
        F.col("threshold").cast("string"), "status", "action",
        F.col("run_ts").cast("timestamp"),
    )


def _recon_for_sql(df):
    return _latest_run(df, SQLSERVER_CHECK_PREFIXES).select(
        "run_id", "check_id", "description", "expected", "actual", "difference", "status",
        F.col("run_ts").cast("timestamp"),
    )


def _write_table(df, table, database):
    (df.coalesce(1).write.format("jdbc")
        .option("url", jdbc_url(database)).options(**jdbc_options())
        .option("dbtable", f"churn.{table}")
        .option("truncate", "true").option("batchsize", 5000)
        .mode("overwrite").save())


def load_tables(spark, warehouse_dir=WAREHOUSE_DIR, gold_dir=GOLD_DIR, dq_results_path=DQ_RESULTS_PATH,
                recon_results_path=RECON_RESULTS_PATH, database="churn_db",
                scores_path=MODEL_SCORES_PATH):
    """Truncate and reload every table from Delta. Returns {table: {"rows": n, "seconds": s}}."""
    def delta(path):
        p = Path(path).resolve().as_posix()
        return spark.read.format("delta").load(p) if (Path(path) / "_delta_log").is_dir() else None

    sources = {t: delta(Path(warehouse_dir) / t) for t in WAREHOUSE_TABLES}
    sources.update({t: delta(Path(gold_dir) / t) for t in GOLD_TABLES.values()})
    sources["customer_scores"] = delta(scores_path)
    dq, recon = delta(dq_results_path), delta(recon_results_path)
    sources["dq_results"] = None if dq is None else _dq_for_sql(dq)
    sources["reconciliation_results"] = None if recon is None else _recon_for_sql(recon)

    loaded = {}
    for table, df in sources.items():
        if df is None:
            logger.info("Skipping %s: no Delta table", table)
            continue
        for col, dtype in CASTS.get(table, {}).items():
            df = df.withColumn(col, F.col(col).cast(dtype))
        start = time.perf_counter()
        rows = df.count()
        _write_table(df, table, database)
        loaded[table] = {"rows": rows, "seconds": round(time.perf_counter() - start, 1)}
        logger.info("Loaded %s: %d rows", table, rows)
    return loaded


def load_and_check(spark, warehouse_dir=WAREHOUSE_DIR, gold_dir=GOLD_DIR, silver_path=SILVER_PATH,
                   dq_results_path=DQ_RESULTS_PATH, recon_results_path=RECON_RESULTS_PATH,
                   snapshot_month=SNAPSHOT_MONTH, database="churn_db", run_id=None,
                   report_json=SQLSERVER_REPORT_JSON, evidence_dir=EVIDENCE_DIR):
    """Load, then run rc09/rc10/rc13/rc14. Returns (loaded, results); results are recorded
    even when a check fails, and the ReconciliationError is raised after the evidence is written."""
    run_id = run_id or uuid.uuid4().hex
    start = time.perf_counter()
    loaded = load_tables(spark, warehouse_dir, gold_dir, dq_results_path, recon_results_path, database)
    tables = {t: v["rows"] for t, v in loaded.items()}
    error, results = None, []
    try:
        results = run_and_record(spark, tables, silver_path, gold_dir, snapshot_month, database,
                                 run_id, recon_results_path, report_json)
    except ReconciliationError as e:
        error = e
        results = _read_report(report_json)
    elapsed = time.perf_counter() - start
    write_evidence("sqlserver_load.json", {
        "run_id": run_id, "timestamp": datetime.now().astimezone().isoformat(),
        "database": database, "snapshot_month": snapshot_month, "tables": loaded,
        "total_seconds": round(elapsed, 1),
        "checks": {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "fail")},
    }, evidence_dir)
    if error:
        raise error
    return loaded, results


def _read_report(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["results"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-month", default=SNAPSHOT_MONTH, help="yyyy-mm")
    args = parser.parse_args()
    start = time.perf_counter()
    spark = get_spark()
    code, loaded, results = 0, {}, []
    try:
        loaded, results = load_and_check(spark, snapshot_month=args.snapshot_month,
                                         database=os.environ.get("SQLSERVER_DB", "churn_db"))
    except ReconciliationError as e:
        print(e)
        code = 1
    finally:
        spark.stop()
    for table, info in loaded.items():
        print(f"{table:<34}{info['rows']:>8} rows {info['seconds']:>6.1f}s")
    for r in results:
        print(f"{r['check_id']:<36}{r['status']}")
    print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
    sys.exit(code)
