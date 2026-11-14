import pytest
from pyspark.sql import functions as F
from pyspark.sql import types as T

from src.config.settings import EXPECTED_RAW_COLUMNS, GOLD_OVERALL, GOLD_SEGMENTS, METADATA_COLUMNS, RAW_CSV
from src.ingestion.bronze_loader import load_bronze
from src.transformations.gold import overall_table, segment_table, write_gold
from src.transformations.silver import build_silver, write_silver

BASE = {
    "customerID": "7590-VHVEG", "gender": "Female", "SeniorCitizen": "0", "Partner": "Yes",
    "Dependents": "No", "tenure": "1", "PhoneService": "No", "MultipleLines": "No phone service",
    "InternetService": "DSL", "OnlineSecurity": "No", "OnlineBackup": "Yes",
    "DeviceProtection": "No", "TechSupport": "No", "StreamingTV": "No", "StreamingMovies": "No",
    "Contract": "Month-to-month", "PaperlessBilling": "Yes", "PaymentMethod": "Electronic check",
    "MonthlyCharges": "29.85", "TotalCharges": "29.85", "Churn": "No",
}
ADDONS = ["OnlineSecurity", "OnlineBackup", "DeviceProtection", "TechSupport",
          "StreamingTV", "StreamingMovies"]

SILVER_COLUMNS = [
    "customer_id", "gender", "senior_citizen", "partner", "dependents", "tenure_months",
    "phone_service", "multiple_lines", "internet_service", "online_security", "online_backup",
    "device_protection", "tech_support", "streaming_tv", "streaming_movies", "contract",
    "paperless_billing", "payment_method", "monthly_charges", "total_charges", "churn",
    "total_charges_imputed", "churn_flag", "has_internet", "tenure_band",
    "addon_service_count", "_ingest_ts", "_source_file", "_batch_id",
]


def bronze_df(spark, overrides_list):
    rows = []
    for i, ov in enumerate(overrides_list):
        r = {**BASE, "customerID": f"id-{i}", **ov}
        rows.append(tuple(r[c] for c in EXPECTED_RAW_COLUMNS) + ("2026-01-01", "f.csv", "b1"))
    df = spark.createDataFrame(rows, EXPECTED_RAW_COLUMNS + METADATA_COLUMNS)
    return df.withColumn("_ingest_ts", F.to_timestamp("_ingest_ts"))


def silver_rows(spark, overrides_list):
    return build_silver(bronze_df(spark, overrides_list)).orderBy("customer_id").collect()


def test_silver_columns_and_types(spark):
    df = build_silver(bronze_df(spark, [{}]))
    assert df.columns == SILVER_COLUMNS
    types = {f.name: f.dataType for f in df.schema.fields}
    for c in ["senior_citizen", "partner", "dependents", "phone_service", "paperless_billing",
              "churn", "total_charges_imputed", "has_internet"]:
        assert types[c] == T.BooleanType(), c
    assert types["tenure_months"] == T.IntegerType()
    assert types["monthly_charges"] == T.DoubleType()
    assert types["total_charges"] == T.DoubleType()
    assert types["churn_flag"] == T.IntegerType()
    assert types["addon_service_count"] == T.IntegerType()
    assert types["multiple_lines"] == T.StringType()
    assert types["tenure_band"] == T.StringType()
    assert types["_ingest_ts"] == T.TimestampType()


def test_total_charges(spark):
    a, b = silver_rows(spark, [
        {"TotalCharges": "29.85"},
        {"TotalCharges": " ", "tenure": "0"},
    ])
    assert a.total_charges == 29.85 and a.total_charges_imputed is False
    assert b.total_charges == 0.0 and b.total_charges_imputed is True


def test_booleans_and_three_state(spark):
    a, b = silver_rows(spark, [
        {"SeniorCitizen": "1", "Partner": "Yes", "Dependents": "Yes", "PhoneService": "Yes",
         "PaperlessBilling": "Yes", "Churn": "Yes"},
        {"SeniorCitizen": "0", "Partner": "No", "Dependents": "No", "PhoneService": "No",
         "PaperlessBilling": "No", "Churn": "No"},
    ])
    for c in ["senior_citizen", "partner", "dependents", "phone_service", "paperless_billing", "churn"]:
        assert a[c] is True and b[c] is False, c
    assert a.multiple_lines == "No phone service"
    assert a.churn_flag == 1 and b.churn_flag == 0


def test_tenure_band_boundaries(spark):
    tenures = [0, 1, 12, 13, 24, 25, 48, 49, 72, 73, 300]
    expected = ["0-12", "0-12", "0-12", "13-24", "13-24", "25-48", "25-48", "49-72", "49-72", "73+", "73+"]
    df = build_silver(bronze_df(spark, [{"tenure": str(t), "TotalCharges": "1.0"} for t in tenures]))
    got = {r.tenure_months: r.tenure_band for r in df.collect()}
    assert [got[t] for t in tenures] == expected


def test_addon_service_count(spark):
    none = {c: "No internet service" for c in ADDONS}
    mixed = {**{c: "No" for c in ADDONS}, "OnlineSecurity": "Yes", "StreamingTV": "Yes",
             "TechSupport": "Yes"}
    full = {c: "Yes" for c in ADDONS}
    rows = silver_rows(spark, [none, mixed, full])
    assert [r.addon_service_count for r in rows] == [0, 3, 6]


def test_has_internet(spark):
    a, b, c = silver_rows(spark, [
        {"InternetService": "DSL"}, {"InternetService": "Fiber optic"}, {"InternetService": "No"},
    ])
    assert (a.has_internet, b.has_internet, c.has_internet) == (True, True, False)


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_write_silver_rejects_bad_total_charges(spark, tmp_path):
    bronze = tmp_path / "bronze"
    bronze_df(spark, [{"TotalCharges": "abc"}]).write.format("delta").save(bronze.resolve().as_posix())
    with pytest.raises(ValueError, match="1 rows"):
        write_silver(spark, bronze_path=bronze, silver_path=tmp_path / "silver")


def silver_for_gold(spark, data):
    return spark.createDataFrame(data, "seg string, churn_flag int")


def test_segment_table(spark):
    df = silver_for_gold(spark, [("a", 1), ("a", 0), ("a", 0), ("b", 1)])
    out = segment_table(df, "seg")
    assert [(f.name, f.dataType) for f in out.schema.fields] == [
        ("segment", T.StringType()), ("customers", T.LongType()),
        ("churned", T.LongType()), ("churn_rate_pct", T.DoubleType()),
    ]
    assert [tuple(r) for r in out.collect()] == [("a", 3, 1, 33.33), ("b", 1, 1, 100.0)]


def test_overall_table_empty(spark):
    out = overall_table(silver_for_gold(spark, []))
    assert [tuple(r) for r in out.collect()] == [("All", 0, 0, 0.0)]
    assert [f.dataType for f in out.schema.fields] == [
        T.StringType(), T.LongType(), T.LongType(), T.DoubleType()]


# ---- integration on the real CSV ----

@pytest.fixture(scope="module")
def gold(spark, tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline")
    load_bronze(spark, bronze_path=root / "bronze", batch_id="test")
    write_silver(spark, bronze_path=root / "bronze", silver_path=root / "silver")
    write_gold(spark, silver_path=root / "silver", gold_dir=root / "gold")
    silver = spark.read.format("delta").load((root / "silver").resolve().as_posix())
    tables = {
        n: spark.read.format("delta").load((root / "gold" / n).resolve().as_posix())
        for n in [GOLD_OVERALL, *GOLD_SEGMENTS]
    }
    return silver, tables


needs_csv = pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV missing")


def rows_of(df):
    return {r.segment: (r.customers, r.churned, r.churn_rate_pct) for r in df.collect()}


@needs_csv
@pytest.mark.integration
def test_real_silver(gold):
    silver, _ = gold
    assert silver.count() == 7043
    assert silver.filter("total_charges_imputed").count() == 11
    assert silver.filter(F.col("total_charges").isNull()).count() == 0


@needs_csv
@pytest.mark.integration
def test_real_overall(gold):
    _, t = gold
    assert rows_of(t[GOLD_OVERALL]) == {"All": (7043, 1869, 26.54)}


@needs_csv
@pytest.mark.parametrize("name", list(GOLD_SEGMENTS))
@pytest.mark.integration
def test_real_segment_sums(gold, name):
    _, t = gold
    r = t[name].agg(F.sum("customers"), F.sum("churned")).first()
    assert (r[0], r[1]) == (7043, 1869)


@needs_csv
@pytest.mark.integration
def test_real_baseline_segments(gold):
    _, t = gold
    assert rows_of(t["gold_churn_by_internet_service"]) == {
        "Fiber optic": (3096, 1297, 41.89), "DSL": (2421, 459, 18.96), "No": (1526, 113, 7.4)}
    assert rows_of(t["gold_churn_by_contract"]) == {
        "Month-to-month": (3875, 1655, 42.71), "One year": (1473, 166, 11.27),
        "Two year": (1695, 48, 2.83)}
    assert rows_of(t["gold_churn_by_payment_method"]) == {
        "Electronic check": (2365, 1071, 45.29), "Mailed check": (1612, 308, 19.11),
        "Bank transfer (automatic)": (1544, 258, 16.71),
        "Credit card (automatic)": (1522, 232, 15.24)}


@needs_csv
@pytest.mark.integration
def test_real_tenure_band_rows(gold):
    _, t = gold
    assert t["gold_churn_by_tenure_band"].count() == 4


def test_unlabelled_churn_stays_null(spark):
    df = bronze_df(spark, [{"Churn": "Yes"}, {"Churn": "No"}]).drop("Churn")
    silver = build_silver(df)
    assert silver.columns == SILVER_COLUMNS
    assert all(r.churn is None and r.churn_flag is None for r in silver.collect())
    overall = overall_table(silver).first()
    assert overall.customers == 2 and overall.churned is None and overall.churn_rate_pct is None
    seg = segment_table(silver, "contract").first()
    assert seg.churned is None and seg.churn_rate_pct is None
