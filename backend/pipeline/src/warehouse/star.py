import argparse
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from pyspark.sql import Window
from pyspark.sql import functions as F

from src.config.storage import delta_exists, delta_table, read_delta, write_delta
from src.config.settings import SILVER_PATH, SNAPSHOT_MONTH, WAREHOUSE_DIR
from src.ingestion.spark_session import get_spark
from src.output.evidence import write_evidence

logger = logging.getLogger(__name__)

CUSTOMER_ATTRS = ["gender", "senior_citizen", "partner", "dependents"]
# service_profile_key = xxhash64(concat_ws('|', <these columns in this order, cast to string>))
PROFILE_COLUMNS = [
    "contract", "internet_service", "phone_service", "multiple_lines", "online_security",
    "online_backup", "device_protection", "tech_support", "streaming_tv", "streaming_movies",
    "paperless_billing", "payment_method",
]


def month_key(month):
    """'2026-10' -> 20261001 (first day of the month as yyyymmdd)."""
    parsed = datetime.strptime(month, "%Y-%m")
    return parsed.year * 10000 + parsed.month * 100 + 1


def _path(warehouse_dir, table):
    return (Path(warehouse_dir) / table).resolve().as_posix()


def _read(spark, path):
    return read_delta(spark, path)


def _with_profile_key(df):
    text = [F.col(c).cast("string") for c in PROFILE_COLUMNS]
    return df.withColumn("service_profile_key", F.xxhash64(F.concat_ws("|", *text)))


def build_dim_date(spark):
    dates = spark.sql(
        "SELECT explode(sequence(to_date('2020-01-01'), to_date('2030-12-31'), interval 1 day)) AS full_date"
    )
    return dates.select(
        F.date_format("full_date", "yyyyMMdd").cast("int").alias("date_key"),
        "full_date",
        F.year("full_date").cast("smallint").alias("year"),
        F.quarter("full_date").cast("tinyint").alias("quarter"),
        F.month("full_date").cast("tinyint").alias("month"),
        F.date_format("full_date", "MMMM").alias("month_name"),
        F.date_format("full_date", "yyyy-MM").alias("year_month"),
        (F.dayofmonth("full_date") == 1).alias("is_month_start"),
    )


def upsert_dim_customer(spark, silver, path, date_key):
    """SCD type 1 on attributes; customer_key is assigned once and never changes."""
    current = silver.select("customer_id", *CUSTOMER_ATTRS).dropDuplicates(["customer_id"])
    exists = delta_exists(spark, path)
    max_key, new = 0, current
    known = None
    if exists:
        target = _read(spark, path)
        keys = target.select("customer_id", "customer_key")
        max_key = target.agg(F.max("customer_key")).first()[0] or 0
        known = current.join(keys, "customer_id")
        new = current.join(keys, "customer_id", "left_anti")
    new = new.withColumn(
        "customer_key", F.row_number().over(Window.orderBy("customer_id")).cast("long") + F.lit(max_key)
    )
    source = new if known is None else known.unionByName(new)
    source = source.select(
        "customer_key", "customer_id", *CUSTOMER_ATTRS,
        F.lit(date_key).alias("first_seen_date_key"), F.lit(date_key).alias("last_seen_date_key"),
    )
    if not exists:
        write_delta(source, path)
        return
    updates = {a: f"s.{a}" for a in CUSTOMER_ATTRS}
    updates["first_seen_date_key"] = "least(t.first_seen_date_key, s.first_seen_date_key)"
    updates["last_seen_date_key"] = "greatest(t.last_seen_date_key, s.last_seen_date_key)"
    (delta_table(spark, path).alias("t")
        .merge(source.alias("s"), "t.customer_id = s.customer_id")
        .whenMatchedUpdate(set=updates)
        .whenNotMatchedInsertAll()
        .execute())


def upsert_dim_service_profile(spark, silver, path):
    """Junk dimension: one row per distinct combination, insert-if-absent."""
    source = (_with_profile_key(silver).select("service_profile_key", *PROFILE_COLUMNS)
              .dropDuplicates(["service_profile_key"]))
    if not delta_exists(spark, path):
        write_delta(source, path)
        return
    (delta_table(spark, path).alias("t")
        .merge(source.alias("s"), "t.service_profile_key = s.service_profile_key")
        .whenNotMatchedInsertAll()
        .execute())


def write_fact_snapshot(spark, silver, customer_path, path, date_key, run_id):
    keys = _read(spark, customer_path).select("customer_id", "customer_key")
    fact = _with_profile_key(silver).join(keys, "customer_id").select(
        F.lit(date_key).alias("snapshot_date_key"),
        "customer_key",
        "service_profile_key",
        "tenure_months",
        "tenure_band",
        "addon_service_count",
        F.col("monthly_charges").cast("decimal(8,2)").alias("monthly_charges"),
        F.col("total_charges").cast("decimal(10,2)").alias("total_charges"),
        "total_charges_imputed",
        "churn_flag",
        F.lit(None).cast("decimal(5,4)").alias("risk_score"),
        F.lit(None).cast("string").alias("risk_band"),
        F.lit(False).alias("is_synthetic"),
        F.lit(run_id).alias("run_id"),
    )
    replace = f"snapshot_date_key = {date_key}" if delta_exists(spark, path) else None
    write_delta(fact, path, partition_by="snapshot_date_key", replace_where=replace)


def build_warehouse(spark, silver_path=SILVER_PATH, warehouse_dir=WAREHOUSE_DIR,
                    snapshot_month=SNAPSHOT_MONTH, run_id=None):
    run_id = run_id or uuid.uuid4().hex
    date_key = month_key(snapshot_month)
    silver = _read(spark, Path(silver_path).resolve().as_posix())

    def path(table):
        return _path(warehouse_dir, table)

    upsert_dim_customer(spark, silver, path("dim_customer"), date_key)
    upsert_dim_service_profile(spark, silver, path("dim_service_profile"))
    # dim_date is deterministic (fixed 2020-2030 range), so build it once; delete the
    # table to rebuild it if the range ever changes.
    if not delta_exists(spark, path("dim_date")):
        write_delta(build_dim_date(spark).coalesce(1), path("dim_date"), overwrite_schema=True)
    write_fact_snapshot(spark, silver, path("dim_customer"), path("fact_customer_snapshot"),
                        date_key, run_id)

    counts = {t: _read(spark, path(t)).count() for t in (
        "dim_customer", "dim_service_profile", "dim_date", "fact_customer_snapshot")}
    counts["fact_customer_snapshot_month"] = _read(spark, path("fact_customer_snapshot")).filter(
        F.col("snapshot_date_key") == date_key).count()
    logger.info("Warehouse built in %s: %s", warehouse_dir, counts)
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-month", default=SNAPSHOT_MONTH, help="yyyy-mm")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", args.snapshot_month):
        parser.error("--snapshot-month must look like 2026-10")
    start = time.perf_counter()
    spark = get_spark()
    try:
        run_id = uuid.uuid4().hex
        counts = build_warehouse(spark, snapshot_month=args.snapshot_month, run_id=run_id)
        elapsed = time.perf_counter() - start
        for name, n in counts.items():
            print(f"{name}: {n}")
        print(f"Snapshot month: {args.snapshot_month}")
        print(f"Elapsed seconds: {elapsed:.1f}")
        write_evidence("warehouse_build.json", {
            "run_id": run_id, "snapshot_month": args.snapshot_month,
            "timestamp": datetime.now().astimezone().isoformat(),
            "rows": counts, "seconds": round(elapsed, 1),
        })
    finally:
        spark.stop()
