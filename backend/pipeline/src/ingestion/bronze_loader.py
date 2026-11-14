import logging
import time
import uuid
from pathlib import Path

from pyspark.sql import functions as F

from src.config.storage import read_delta, write_delta
from src.config.settings import BRONZE_PATH, EXPECTED_RAW_COLUMNS, RAW_CSV, REQUIRED_RAW_COLUMNS
from src.ingestion.spark_session import get_spark

logger = logging.getLogger(__name__)


def read_raw(spark, csv_path):
    # No inferSchema: every column stays string. Whitespace trimming is off so a
    # blank " " TotalCharges survives as " ".
    return (
        spark.read.option("header", True)
        .option("ignoreLeadingWhiteSpace", False)
        .option("ignoreTrailingWhiteSpace", False)
        .csv(Path(csv_path).resolve().as_posix())
    )


class ColumnMismatch(ValueError):
    def __init__(self, missing, extra):
        super().__init__(f"Column mismatch. Missing: {missing}. Extra: {extra}")
        self.missing, self.extra = missing, extra


def validate_columns(df):
    missing = [c for c in REQUIRED_RAW_COLUMNS if c not in df.columns]
    extra = [c for c in df.columns if c not in EXPECTED_RAW_COLUMNS]
    if missing or extra:
        raise ColumnMismatch(missing, extra)


def add_metadata(df, source_file, batch_id):
    return (
        df.withColumn("_ingest_ts", F.current_timestamp())
        .withColumn("_source_file", F.lit(source_file))
        .withColumn("_batch_id", F.lit(batch_id))
    )


def load_bronze(spark, csv_path=RAW_CSV, bronze_path=BRONZE_PATH, batch_id=None):
    batch_id = batch_id or uuid.uuid4().hex
    out = Path(bronze_path).resolve().as_posix()

    df = read_raw(spark, csv_path)
    validate_columns(df)
    df = add_metadata(df, Path(csv_path).name, batch_id)

    write_delta(df, bronze_path, overwrite_schema=True)

    count = read_delta(spark, bronze_path).count()
    logger.info("Bronze written to %s (batch %s): %d rows", out, batch_id, count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start = time.perf_counter()
    spark = get_spark()
    csv_rows = read_raw(spark, RAW_CSV).count()
    bronze_rows = load_bronze(spark)
    cols = len(read_delta(spark, BRONZE_PATH).columns)
    elapsed = time.perf_counter() - start
    print(f"CSV rows: {csv_rows}")
    print(f"Bronze rows: {bronze_rows}")
    print(f"Columns: {cols}")
    print(f"Elapsed seconds: {elapsed:.1f}")
    spark.stop()
