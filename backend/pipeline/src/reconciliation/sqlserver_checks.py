import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from pyspark.sql import functions as F

from src.config.settings import RECON_RESULTS_PATH, SILVER_PATH, SQLSERVER_REPORT_JSON
from src.config.sqlserver import jdbc_options, jdbc_url
from src.reconciliation.checks import GOLD_TABLES, _result, enforce
from src.reconciliation.report import write_results
from src.warehouse.star import month_key

logger = logging.getLogger(__name__)

ORPHAN_CHECKS = [  # (check id, description, fact column, dimension table, dimension column)
    ("rc13.fact_customer", "fact_customer_snapshot rows without a dim_customer row",
     "customer_key", "dim_customer", "customer_key"),
    ("rc13.fact_service", "fact_customer_snapshot rows without a dim_service_profile row",
     "service_profile_key", "dim_service_profile", "service_profile_key"),
    ("rc13.dim_date", "fact_customer_snapshot rows without a dim_date row",
     "snapshot_date_key", "dim_date", "date_key"),
]


def _sql(spark, database, query):
    return (spark.read.format("jdbc").option("url", jdbc_url(database)).options(**jdbc_options())
            .option("query", query).load())


def _rate(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


def run_sqlserver_checks(spark, tables, silver_path, gold_dir, snapshot_month, database, run_id):
    """tables: {table name: Delta row count} for every table that was loaded."""
    run_ts = datetime.now(timezone.utc).isoformat()
    results = []
    load = lambda p: spark.read.format("delta").load(Path(p).resolve().as_posix())  # noqa: E731

    for table, delta_rows in tables.items():
        actual = _sql(spark, database, f"SELECT COUNT(*) AS n FROM churn.{table}").first()["n"]
        results.append(_result(run_id, run_ts, f"rc09.{table}",
                               f"SQL Server {table} row count = Delta row count",
                               delta_rows, actual, actual - delta_rows, actual == delta_rows))

    for short, table in GOLD_TABLES.items():
        cols = "segment, customers, churned, churn_rate_pct"
        exp = sorted((r["segment"], r["customers"], r["churned"], str(_rate(r["churn_rate_pct"])))
                     for r in load(Path(gold_dir) / table).collect())
        act = sorted((r["segment"], r["customers"], r["churned"], str(_rate(r["churn_rate_pct"])))
                     for r in _sql(spark, database, f"SELECT {cols} FROM churn.{table}").collect())
        mismatches = len(set(exp) ^ set(act))
        results.append(_result(run_id, run_ts, f"rc10.{short}",
                               f"SQL Server {table} = Delta Gold (segment, customers, churned, rate)",
                               json.dumps(exp), json.dumps(act), mismatches, exp == act))

    for check_id, description, column, dim, dim_column in ORPHAN_CHECKS:
        orphans = _sql(
            spark, database,
            f"SELECT COUNT(*) AS n FROM churn.fact_customer_snapshot f WHERE NOT EXISTS "
            f"(SELECT 1 FROM churn.{dim} d WHERE d.{dim_column} = f.{column})",
        ).first()["n"]
        results.append(_result(run_id, run_ts, check_id, description, 0, orphans, orphans, orphans == 0))

    silver = load(silver_path)
    expected = (silver.count(), silver.agg(F.sum("churn_flag")).first()[0] or 0)
    row = _sql(
        spark, database,
        "SELECT COUNT(*) AS n, COALESCE(SUM(CAST(churn_flag AS INT)), 0) AS churned "
        f"FROM churn.fact_customer_snapshot WHERE snapshot_date_key = {month_key(snapshot_month)}",
    ).first()
    actual = (row["n"], row["churned"])
    diff = abs(actual[0] - expected[0]) + abs(actual[1] - expected[1])
    results.append(_result(
        run_id, run_ts, "rc14",
        f"fact_customer_snapshot {snapshot_month}: rows and churned = Silver",
        f"rows={expected[0]}, churned={expected[1]}", f"rows={actual[0]}, churned={actual[1]}",
        diff, diff == 0))
    return results


def run_and_record(spark, tables, silver_path=SILVER_PATH, gold_dir=None, snapshot_month=None,
                   database="churn_db", run_id=None, results_path=RECON_RESULTS_PATH,
                   report_json=SQLSERVER_REPORT_JSON):
    results = run_sqlserver_checks(spark, tables, silver_path, gold_dir, snapshot_month, database, run_id)
    write_results(spark, results, results_path, report_json)
    enforce(results)
    return results

