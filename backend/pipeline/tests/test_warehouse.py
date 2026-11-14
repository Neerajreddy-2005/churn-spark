import logging
import socket

import pytest
from pyspark.sql import functions as F

from src.config.settings import DQ_RESULTS_PATH, GOLD_DIR, RAW_CSV, SILVER_PATH
from src.config.sqlserver import jdbc_options, jdbc_url
from src.output.sqlserver_setup import drop_database, run_ddl, split_batches
from src.output.sqlserver_writer import load_and_check
from src.warehouse.star import (
    build_dim_date, build_warehouse, month_key, upsert_dim_customer, upsert_dim_service_profile,
    write_fact_snapshot,
)

SILVER_COLUMNS = [
    "customer_id", "gender", "senior_citizen", "partner", "dependents", "tenure_months",
    "phone_service", "multiple_lines", "internet_service", "online_security", "online_backup",
    "device_protection", "tech_support", "streaming_tv", "streaming_movies", "contract",
    "paperless_billing", "payment_method", "monthly_charges", "total_charges",
    "total_charges_imputed", "churn_flag", "tenure_band", "addon_service_count",
]


def silver_row(customer_id, **over):
    row = dict(
        customer_id=customer_id, gender="Female", senior_citizen=False, partner=True,
        dependents=False, tenure_months=5, phone_service=True, multiple_lines="No",
        internet_service="DSL", online_security="No", online_backup="Yes",
        device_protection="No", tech_support="No", streaming_tv="No", streaming_movies="No",
        contract="Month-to-month", paperless_billing=True, payment_method="Mailed check",
        monthly_charges=29.85, total_charges=149.25, total_charges_imputed=False,
        churn_flag=0, tenure_band="0-12", addon_service_count=1,
    )
    row.update(over)
    return tuple(row[c] for c in SILVER_COLUMNS)


def table(spark, wh, name):
    return spark.read.format("delta").load((wh / name).resolve().as_posix())


# ---- unit ----

def silver_df(spark, rows):
    return spark.createDataFrame(rows, SILVER_COLUMNS)


def build_month(spark, wh, rows, month, run_id):
    """Dims + fact for one month, without the (slow, unchanged) dim_date build."""
    df, key = silver_df(spark, rows), month_key(month)
    customer = (wh / "dim_customer").resolve().as_posix()
    upsert_dim_customer(spark, df, customer, key)
    upsert_dim_service_profile(spark, df, (wh / "dim_service_profile").resolve().as_posix())
    write_fact_snapshot(spark, df, customer, (wh / "fact_customer_snapshot").resolve().as_posix(),
                        key, run_id)


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_dim_customer_keys_stable_and_new_only(spark, tmp_path):
    wh = tmp_path / "wh"
    build_month(spark, wh, [silver_row("b"), silver_row("a")], "2026-10", "r1")
    first = {r["customer_id"]: r["customer_key"] for r in table(spark, wh, "dim_customer").collect()}
    assert first == {"a": 1, "b": 2}

    build_month(spark, wh, [silver_row("b", gender="Male"), silver_row("c"), silver_row("d")],
                "2026-11", "r2")
    rows = {r["customer_id"]: r for r in table(spark, wh, "dim_customer").collect()}
    assert rows["a"]["customer_key"] == 1 and rows["b"]["customer_key"] == 2
    assert (rows["c"]["customer_key"], rows["d"]["customer_key"]) == (3, 4)
    assert rows["b"]["gender"] == "Male"  # SCD type 1: overwritten
    assert (rows["b"]["first_seen_date_key"], rows["b"]["last_seen_date_key"]) == (20261001, 20261101)
    assert rows["a"]["last_seen_date_key"] == 20261001  # absent in month 2, untouched


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_service_profile_key_deterministic_and_deduped(spark, tmp_path):
    wh = tmp_path / "wh"
    rows = [silver_row("a"), silver_row("b"), silver_row("c", contract="Two year")]
    build_month(spark, wh, rows, "2026-10", "r1")
    keys = {r["contract"]: r["service_profile_key"] for r in table(spark, wh, "dim_service_profile").collect()}
    assert set(keys) == {"Month-to-month", "Two year"}
    fact = {r["customer_key"]: r["service_profile_key"] for r in table(spark, wh, "fact_customer_snapshot").collect()}
    assert fact[1] == fact[2] == keys["Month-to-month"] and fact[3] == keys["Two year"]

    # same input in a fresh warehouse gives the same keys; a second month adds no duplicates
    upsert_dim_service_profile(spark, silver_df(spark, rows), (tmp_path / "wh2" / "p").resolve().as_posix())
    again = {r["contract"]: r["service_profile_key"]
             for r in spark.read.format("delta").load((tmp_path / "wh2" / "p").resolve().as_posix()).collect()}
    assert again == keys
    upsert_dim_service_profile(spark, silver_df(spark, rows), (wh / "dim_service_profile").resolve().as_posix())
    assert table(spark, wh, "dim_service_profile").count() == 2


def test_dim_date_boundaries(spark):
    df = build_dim_date(spark)
    first, last = df.orderBy("full_date").first(), df.orderBy(F.col("full_date").desc()).first()
    assert (first["date_key"], str(first["full_date"])) == (20200101, "2020-01-01")
    assert (last["date_key"], str(last["full_date"])) == (20301231, "2030-12-31")
    assert df.count() == 4018 and df.select("date_key").distinct().count() == 4018
    row = df.filter("date_key = 20261001").first()
    assert (row["year"], row["quarter"], row["month"]) == (2026, 4, 10)
    assert (row["month_name"], row["year_month"], row["is_month_start"]) == ("October", "2026-10", True)
    assert month_key("2026-10") == 20261001


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_snapshot_replace_where_is_idempotent_per_month(spark, tmp_path):
    wh = tmp_path / "wh"
    build_month(spark, wh, [silver_row("a"), silver_row("b")], "2026-10", "A1")
    build_month(spark, wh, [silver_row("a"), silver_row("b"), silver_row("c")], "2026-11", "B1")
    build_month(spark, wh, [silver_row("a", churn_flag=1), silver_row("b")], "2026-10", "A2")

    fact = table(spark, wh, "fact_customer_snapshot")
    by_month = {r["snapshot_date_key"]: r["n"] for r in fact.groupBy("snapshot_date_key").agg(
        F.count("*").alias("n")).collect()}
    assert by_month == {20261001: 2, 20261101: 3}
    assert {r["run_id"] for r in fact.filter("snapshot_date_key = 20261001").collect()} == {"A2"}
    assert {r["run_id"] for r in fact.filter("snapshot_date_key = 20261101").collect()} == {"B1"}
    assert fact.filter("snapshot_date_key = 20261001").agg(F.sum("churn_flag")).first()[0] == 1


def test_jdbc_url_integrated(monkeypatch):
    monkeypatch.setenv("SQLSERVER_AUTH", "integrated")
    monkeypatch.setenv("SQLSERVER_HOST", "h")
    monkeypatch.setenv("SQLSERVER_PORT", "1500")
    url = jdbc_url("db1")
    assert url.startswith("jdbc:sqlserver://h:1500;databaseName=db1;")
    assert "integratedSecurity=true" in url
    assert "password" not in url.lower()
    assert jdbc_options() == {"driver": "com.microsoft.sqlserver.jdbc.SQLServerDriver"}


def test_jdbc_sql_auth(monkeypatch, caplog):
    monkeypatch.setenv("SQLSERVER_AUTH", "sql")
    monkeypatch.delenv("SQLSERVER_PASSWORD", raising=False)
    monkeypatch.setenv("SQLSERVER_USER", "u")
    with pytest.raises(ValueError, match="SQLSERVER_PASSWORD"):
        jdbc_url("db1")
    with pytest.raises(ValueError):
        jdbc_options()

    monkeypatch.setenv("SQLSERVER_PASSWORD", "s3cret-pw")
    with caplog.at_level(logging.DEBUG):
        url = jdbc_url("db1")
    assert "integratedSecurity" not in url and "s3cret-pw" not in url
    assert jdbc_options()["password"] == "s3cret-pw"
    assert "s3cret-pw" not in caplog.text


def test_split_batches():
    text = "SELECT 1;\nGO\n\n  go  \nSELECT 2;\nSELECT 3;\r\nGO\nSELECT 'GO now';\n"
    assert split_batches(text) == ["SELECT 1;", "SELECT 2;\nSELECT 3;", "SELECT 'GO now';"]


# ---- integration: real SQL Server (separate test database) ----

def sqlserver_up():
    try:
        with socket.create_connection(("localhost", 1433), timeout=2):
            return True
    except OSError:
        return False


TEST_DB = "churn_db_test"


@pytest.fixture(scope="module")
def test_database(spark):
    run_ddl(spark, TEST_DB)
    yield TEST_DB
    drop_database(spark, TEST_DB)


@pytest.mark.integration
@pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV missing")
@pytest.mark.skipif(not sqlserver_up(), reason="SQL Server not reachable on localhost:1433")
def test_load_and_reconcile_against_sql_server(spark, tmp_path, test_database):
    wh = tmp_path / "wh"
    build_warehouse(spark, SILVER_PATH, wh, "2026-10", "itest")
    loaded, results = load_and_check(
        spark, warehouse_dir=wh, gold_dir=GOLD_DIR, silver_path=SILVER_PATH,
        dq_results_path=DQ_RESULTS_PATH, recon_results_path=tmp_path / "recon",
        snapshot_month="2026-10", database=test_database, run_id="itest",
        report_json=tmp_path / "sqlserver_report.json", evidence_dir=tmp_path / "evidence",
    )
    assert "fact_retention_action" not in loaded
    ids = {r["check_id"] for r in results}
    assert {"rc13.fact_customer", "rc13.fact_service", "rc13.dim_date", "rc14"} <= ids
    assert any(i.startswith("rc09.") for i in ids) and any(i.startswith("rc10.") for i in ids)
    assert all(r["status"] == "pass" for r in results), [r for r in results if r["status"] != "pass"]
    assert (tmp_path / "evidence" / "sqlserver_load.json").exists()
