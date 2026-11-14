import argparse
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from pyspark.ml import PipelineModel
from pyspark.sql import functions as F

from src.config.storage import UC_SCHEMA, cache, delta_table, read_delta, write_delta
from src.config.settings import (
    MODEL_DIR, MODEL_SCORES_PATH, MODEL_SCORING_JSON, SILVER_PATH, SNAPSHOT_MONTH, WAREHOUSE_DIR,
)
from src.ingestion.spark_session import get_spark
from src.model.features import churn_probability, prepare
from src.model.reasons import add_reasons
from src.model.report import update_insights
from src.model.train import META_FILE, TRAIN_IDS
from src.output.evidence import write_evidence
from src.warehouse.star import month_key

logger = logging.getLogger(__name__)

# Risk band cut-offs are business choices, not model outputs: high = act now, medium = watch,
# low = leave alone. Change them with the retention team's capacity in mind; the warehouse and the
# scores table are rewritten on every scoring run, so a change applies to all customers at once.
HIGH_CUTOFF = 0.60
MEDIUM_CUTOFF = 0.30


def risk_band(score):
    """Column of probabilities -> 'high' (>= 0.60), 'medium' (>= 0.30) or 'low'."""
    return (F.when(score >= HIGH_CUTOFF, "high")
            .when(score >= MEDIUM_CUTOFF, "medium")
            .otherwise("low"))


def score_customers(spark, model_path=MODEL_DIR, silver_path=SILVER_PATH, scored_at=None):
    model_dir = Path(model_path).resolve()
    meta = json.loads((model_dir / META_FILE).read_text(encoding="utf-8"))
    model = PipelineModel.load(model_dir.as_posix())
    silver = read_delta(spark, silver_path)
    train_ids = (spark.read.parquet((model_dir / TRAIN_IDS).as_posix())
                 .select("customer_id", F.lit(True).alias("in_training_set")))

    scored = model.transform(prepare(silver))
    scored = add_reasons(scored, meta)  # Python UDF over the scaled vector; no collect
    scored = scored.withColumn("risk_score", F.round(churn_probability(), 4))
    return (scored.join(train_ids, "customer_id", "left")
            .select(
                "customer_id",
                F.col("risk_score").cast("decimal(5,4)").alias("risk_score"),
                risk_band(F.col("risk_score")).alias("risk_band"),
                "reason_1", "reason_2", "reason_3",
                F.coalesce("in_training_set", F.lit(False)).alias("in_training_set"),
                F.lit(meta["run_id"]).alias("model_version"),
                F.lit(scored_at or datetime.now()).cast("timestamp").alias("scored_at"),
            ))


def write_scores(scores, path=MODEL_SCORES_PATH):
    out = Path(path).resolve()
    if not UC_SCHEMA:
        out.parent.mkdir(parents=True, exist_ok=True)
    write_delta(scores, out, overwrite_schema=True)


def update_warehouse_scores(spark, scores, warehouse_dir=WAREHOUSE_DIR, snapshot_month=SNAPSHOT_MONTH):
    """MERGE risk_score and risk_band into the fact rows of one snapshot month. Idempotent."""
    date_key = month_key(snapshot_month)
    wh = Path(warehouse_dir)
    customers = (read_delta(spark, wh / "dim_customer")
                 .select("customer_id", "customer_key"))
    source = scores.join(customers, "customer_id").select(
        "customer_key", F.col("risk_score").cast("decimal(5,4)").alias("risk_score"), "risk_band")
    fact = delta_table(spark, wh / "fact_customer_snapshot")
    (fact.alias("t")
        .merge(source.alias("s"),
               f"t.snapshot_date_key = {date_key} AND t.customer_key = s.customer_key")
        .whenMatchedUpdate(set={"risk_score": "s.risk_score", "risk_band": "s.risk_band"})
        .execute())


def band_counts(scores):
    counts = {r["risk_band"]: r["n"] for r in scores.groupBy("risk_band").count().withColumnRenamed("count", "n").collect()}
    return {band: counts.get(band, 0) for band in ("high", "medium", "low")}


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
        write_scores(score_customers(spark))
        scores = cache(read_delta(spark, MODEL_SCORES_PATH))
        update_warehouse_scores(spark, scores, snapshot_month=args.snapshot_month)
        counts, rows = band_counts(scores), scores.count()
        elapsed = time.perf_counter() - start
        print(f"Scored customers: {rows}")
        for band, n in counts.items():
            print(f"{band}: {n}")
        print(f"In training set: {scores.filter('in_training_set').count()}")
        print(f"Elapsed seconds: {elapsed:.1f}")
        write_evidence(Path(MODEL_SCORING_JSON).name, {
            "model_version": scores.select("model_version").first()[0],
            "timestamp": datetime.now().astimezone().isoformat(),
            "snapshot_month": args.snapshot_month, "customers_scored": rows,
            "band_counts": counts, "high_cutoff": HIGH_CUTOFF, "medium_cutoff": MEDIUM_CUTOFF,
            "in_training_set": scores.filter("in_training_set").count(), "seconds": round(elapsed, 1),
        }, Path(MODEL_SCORING_JSON).parent)
        update_insights()
    finally:
        spark.stop()
