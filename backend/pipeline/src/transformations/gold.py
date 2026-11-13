import logging
import time
from pathlib import Path

from pyspark.sql import functions as F

from src.config.settings import GOLD_DIR, GOLD_OVERALL, GOLD_SEGMENTS, SILVER_PATH
from src.ingestion.spark_session import get_spark

logger = logging.getLogger(__name__)


def _with_rate(agg_df):
    return agg_df.select(
        "segment",
        "customers",
        "churned",
        F.when(F.col("customers") == 0, F.lit(0.0))
        .otherwise(F.round(100.0 * F.col("churned") / F.col("customers"), 2))
        .alias("churn_rate_pct"),
    )


def segment_table(silver_df, column):
    agg = silver_df.groupBy(F.col(column).cast("string").alias("segment")).agg(
        F.count(F.lit(1)).alias("customers"),
        F.sum("churn_flag").cast("long").alias("churned"),
    )
    return _with_rate(agg).orderBy("segment")


def overall_table(silver_df):
    agg = silver_df.agg(
        F.count(F.lit(1)).alias("customers"),
        F.coalesce(F.sum("churn_flag"), F.lit(0)).cast("long").alias("churned"),
    ).withColumn("segment", F.lit("All"))
    return _with_rate(agg)


def build_gold(silver_df):
    tables = {GOLD_OVERALL: overall_table(silver_df)}
    for name, column in GOLD_SEGMENTS.items():
        tables[name] = segment_table(silver_df, column)
    return tables


def write_gold(spark, silver_path=SILVER_PATH, gold_dir=GOLD_DIR):
    silver = spark.read.format("delta").load(Path(silver_path).resolve().as_posix())
    counts = {}
    for name, df in build_gold(silver).items():
        out = (Path(gold_dir) / name).resolve().as_posix()
        (df.coalesce(1).write.format("delta").mode("overwrite")
            .option("overwriteSchema", "true").save(out))
        counts[name] = spark.read.format("delta").load(out).count()
        logger.info("Gold table %s written to %s: %d rows", name, out, counts[name])
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start = time.perf_counter()
    spark = get_spark()
    try:
        counts = write_gold(spark)
        for name in counts:
            print(name)
            spark.read.format("delta").load((Path(GOLD_DIR) / name).resolve().as_posix()).show(truncate=False)
        print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
    finally:
        spark.stop()
