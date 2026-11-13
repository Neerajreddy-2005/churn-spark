from pyspark.sql import functions as F

ADDON_COLUMNS = [
    "online_security", "online_backup", "device_protection",
    "tech_support", "streaming_tv", "streaming_movies",
]


def add_features(df):
    tenure = F.col("tenure_months")
    addon_count = sum(F.when(F.col(c) == "Yes", 1).otherwise(0) for c in ADDON_COLUMNS)
    return (
        df.withColumn("churn_flag", F.when(F.col("churn"), 1).otherwise(0).cast("int"))
        .withColumn("has_internet", F.col("internet_service") != "No")
        .withColumn(
            "tenure_band",
            F.when(tenure <= 12, "0-12")
            .when(tenure <= 24, "13-24")
            .when(tenure <= 48, "25-48")
            .otherwise("49-72"),
        )
        .withColumn("addon_service_count", addon_count.cast("int"))
    )
