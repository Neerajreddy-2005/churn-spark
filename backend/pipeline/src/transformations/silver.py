import logging
import time
from pathlib import Path

from pyspark.sql import functions as F

from src.config.settings import BRONZE_PATH, METADATA_COLUMNS, SILVER_PATH
from src.ingestion.spark_session import get_spark
from src.transformations.features import add_features

logger = logging.getLogger(__name__)

DERIVED_COLUMNS = ["churn_flag", "has_internet", "tenure_band", "addon_service_count"]


def _yes(name):
    return F.col(name) == "Yes"


def build_silver(bronze_df):
    blank_new = (F.trim("TotalCharges") == "") & F.coalesce(
        F.expr("try_cast(trim(tenure) AS INT)") == 0, F.lit(False)
    )
    df = bronze_df.select(
        F.col("customerID").alias("customer_id"),
        F.col("gender").alias("gender"),
        (F.col("SeniorCitizen") == "1").alias("senior_citizen"),
        _yes("Partner").alias("partner"),
        _yes("Dependents").alias("dependents"),
        F.expr("try_cast(trim(tenure) AS INT)").alias("tenure_months"),
        _yes("PhoneService").alias("phone_service"),
        F.col("MultipleLines").alias("multiple_lines"),
        F.col("InternetService").alias("internet_service"),
        F.col("OnlineSecurity").alias("online_security"),
        F.col("OnlineBackup").alias("online_backup"),
        F.col("DeviceProtection").alias("device_protection"),
        F.col("TechSupport").alias("tech_support"),
        F.col("StreamingTV").alias("streaming_tv"),
        F.col("StreamingMovies").alias("streaming_movies"),
        F.col("Contract").alias("contract"),
        _yes("PaperlessBilling").alias("paperless_billing"),
        F.col("PaymentMethod").alias("payment_method"),
        F.expr("try_cast(trim(MonthlyCharges) AS DOUBLE)").alias("monthly_charges"),
        F.when(blank_new, F.lit(0.0))
        .otherwise(F.expr("try_cast(trim(TotalCharges) AS DOUBLE)"))
        .alias("total_charges"),
        # Unlabelled file (no Churn column): churn stays null, never False.
        (_yes("Churn") if "Churn" in bronze_df.columns else F.lit(None).cast("boolean")).alias("churn"),
        blank_new.alias("total_charges_imputed"),
        *METADATA_COLUMNS,
    )
    featured = add_features(df)
    base = [c for c in df.columns if c not in METADATA_COLUMNS]
    return featured.select(*base, *DERIVED_COLUMNS, *METADATA_COLUMNS)


def write_silver(spark, bronze_path=BRONZE_PATH, silver_path=SILVER_PATH):
    src = Path(bronze_path).resolve().as_posix()
    out = Path(silver_path).resolve().as_posix()

    silver = build_silver(spark.read.format("delta").load(src))
    bad = silver.filter(
        F.col("total_charges").isNull()
        | F.col("tenure_months").isNull()
        | F.col("monthly_charges").isNull()
    ).count()
    if bad:
        raise ValueError(
            f"{bad} rows have null total_charges, tenure_months or monthly_charges in Silver"
        )

    (silver.write.format("delta").mode("overwrite")
        .option("overwriteSchema", "true").save(out))

    count = spark.read.format("delta").load(out).count()
    logger.info("Silver written to %s: %d rows", out, count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start = time.perf_counter()
    spark = get_spark()
    try:
        bronze_rows = spark.read.format("delta").load(Path(BRONZE_PATH).resolve().as_posix()).count()
        silver_rows = write_silver(spark)
        silver = spark.read.format("delta").load(Path(SILVER_PATH).resolve().as_posix())
        imputed = silver.filter("total_charges_imputed").count()
        print(f"Bronze rows: {bronze_rows}")
        print(f"Silver rows: {silver_rows}")
        print(f"Imputed rows: {imputed}")
        print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
    finally:
        spark.stop()
