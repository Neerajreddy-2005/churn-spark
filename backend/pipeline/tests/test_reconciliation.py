import csv
import json
from decimal import Decimal

import pytest
from pyspark.sql import functions as F

from src.config.settings import EXPECTED_RAW_COLUMNS, RAW_CSV
from src.ingestion.bronze_loader import load_bronze
from src.quality.runner import run_dq
from src.reconciliation.checks import (
    ReconciliationError, enforce, expected_rate, run_reconciliation,
)
from src.reconciliation.source_calc import compute_source_stats
from src.transformations.gold import write_gold
from src.transformations.silver import write_silver

BASE = {
    "customerID": "a", "gender": "Female", "SeniorCitizen": "0", "Partner": "Yes",
    "Dependents": "No", "tenure": "1", "PhoneService": "No", "MultipleLines": "No phone service",
    "InternetService": "DSL", "OnlineSecurity": "No", "OnlineBackup": "Yes",
    "DeviceProtection": "No", "TechSupport": "No", "StreamingTV": "No", "StreamingMovies": "No",
    "Contract": "Month-to-month", "PaperlessBilling": "Yes", "PaymentMethod": "Electronic check",
    "MonthlyCharges": "29.85", "TotalCharges": "29.85", "Churn": "No",
}

EXPECTED_IDS = (
    ["rc01", "rc02", "rc03", "rc04.bronze", "rc04.silver", "rc11", "rc12"]
    + [f"{rc}.{s}" for rc in ("rc05", "rc06", "rc08")
       for s in ("overall", "internet", "contract", "payment", "tenure")]
    + [f"rc07.{s}" for s in ("internet", "contract", "payment", "tenure")]
)


# ---- unit ----

def test_compute_source_stats(tmp_path):
    rows = [
        {"customerID": "a", "tenure": "12", "TotalCharges": "10.10", "Churn": "Yes"},
        {"customerID": "a", "tenure": "13", "TotalCharges": "20.20", "Churn": "No",
         "InternetService": "Fiber optic", "Contract": "Two year", "PaymentMethod": "Mailed check"},
        {"customerID": "b", "tenure": "0", "TotalCharges": " ", "Churn": "No"},
        {"customerID": "c", "tenure": "72", "TotalCharges": "0.05", "Churn": "Yes"},
    ]
    path = tmp_path / "t.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EXPECTED_RAW_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**BASE, **r})

    s = compute_source_stats(path)
    assert s["row_count"] == 4
    assert s["distinct_ids"] == 3
    assert s["churned"] == 2
    assert s["blank_total_charges"] == 1
    assert s["total_charges_sum"] == Decimal("30.35")
    assert s["segments"]["internet"] == {
        "DSL": {"customers": 3, "churned": 2},
        "Fiber optic": {"customers": 1, "churned": 0},
    }
    assert s["segments"]["contract"] == {
        "Month-to-month": {"customers": 3, "churned": 2},
        "Two year": {"customers": 1, "churned": 0},
    }
    assert s["segments"]["payment"] == {
        "Electronic check": {"customers": 3, "churned": 2},
        "Mailed check": {"customers": 1, "churned": 0},
    }
    assert s["segments"]["tenure"] == {
        "0-12": {"customers": 2, "churned": 1},
        "13-24": {"customers": 1, "churned": 0},
        "49-72": {"customers": 1, "churned": 1},
    }


def test_compute_source_stats_unlabelled(tmp_path):
    cols = [c for c in EXPECTED_RAW_COLUMNS if c != "Churn"]
    path = tmp_path / "u.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerow({**BASE, "tenure": "80"})
    s = compute_source_stats(path)
    assert s["labelled"] is False and s["churned"] is None and s["row_count"] == 1
    assert s["segments"]["tenure"] == {"73+": {"customers": 1, "churned": None}}


@pytest.mark.parametrize("customers, churned, expected", [
    (3, 1, "33.33"), (3, 2, "66.67"), (8, 1, "12.50"), (16, 1, "6.25"),
    (400, 1, "0.25"), (200, 1, "0.50"), (0, 0, "0.00"),
])
def test_expected_rate(customers, churned, expected):
    assert expected_rate(customers, churned) == Decimal(expected)


def test_enforce():
    ok = [{"check_id": "rc01", "status": "pass"}]
    enforce(ok)
    with pytest.raises(ReconciliationError, match="rc02"):
        enforce(ok + [{"check_id": "rc02", "status": "fail"}])


# ---- integration on the real CSV ----

needs_csv = pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV missing")


@pytest.fixture(scope="module")
def pipeline(spark, tmp_path_factory):
    root = tmp_path_factory.mktemp("recon")
    load_bronze(spark, bronze_path=root / "bronze", batch_id="test")
    run_dq(spark, bronze_path=root / "bronze", results_path=root / "dq_results",
           report_json=root / "dq_report.json")
    write_silver(spark, bronze_path=root / "bronze", silver_path=root / "silver")
    write_gold(spark, silver_path=root / "silver", gold_dir=root / "gold")
    return root


def paths(root, silver="silver", gold="gold", name="out"):
    return dict(
        bronze_path=root / "bronze", silver_path=root / silver, gold_dir=root / gold,
        dq_report_json=root / "dq_report.json",
        results_path=root / name / "results", report_json=root / name / "report.json",
    )


def read_report(path):
    return {r["check_id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["results"]}


@needs_csv
@pytest.mark.integration
def test_real_run_passes(spark, pipeline):
    p = paths(pipeline, name="ok")
    results = run_reconciliation(spark, **p)
    ids = [r["check_id"] for r in results]
    assert sorted(ids) == sorted(EXPECTED_IDS)
    assert all(r["status"] == "pass" for r in results)
    assert p["report_json"].exists()
    assert spark.read.format("delta").load(p["results_path"].resolve().as_posix()).count() == len(EXPECTED_IDS)


@needs_csv
@pytest.mark.integration
def test_corrupted_silver_fails(spark, pipeline):
    silver = spark.read.format("delta").load((pipeline / "silver").resolve().as_posix())
    victim = silver.filter(F.col("churn")).select("customer_id").first()[0]
    hit = F.col("customer_id") == victim
    bad = (silver.withColumn("churn", F.when(hit, F.lit(False)).otherwise(F.col("churn")))
           .withColumn("churn_flag", F.when(hit, F.lit(0)).otherwise(F.col("churn_flag"))))
    bad.write.format("delta").mode("overwrite").save((pipeline / "silver_bad").resolve().as_posix())
    write_gold(spark, silver_path=pipeline / "silver_bad", gold_dir=pipeline / "gold_bad")

    p = paths(pipeline, silver="silver_bad", gold="gold_bad", name="bad_silver")
    with pytest.raises(ReconciliationError):
        run_reconciliation(spark, **p)
    report = read_report(p["report_json"])
    assert report["rc03"]["status"] == "fail"
    assert report["rc06.overall"]["status"] == "fail"
    assert report["rc01"]["status"] == "pass"
    assert report["rc02"]["status"] == "pass"


@needs_csv
@pytest.mark.integration
def test_wrong_gold_rate_fails(spark, pipeline):
    write_gold(spark, silver_path=pipeline / "silver", gold_dir=pipeline / "gold_rate")
    target = (pipeline / "gold_rate" / "gold_churn_by_contract").resolve().as_posix()
    df = spark.read.format("delta").load(target)
    first = df.orderBy("segment").first()["segment"]
    changed = df.withColumn(
        "churn_rate_pct",
        F.when(F.col("segment") == first, F.col("churn_rate_pct") + F.lit(1.0))
        .otherwise(F.col("churn_rate_pct")),
    ).collect()
    spark.createDataFrame(changed, df.schema).coalesce(1).write.format("delta") \
        .mode("overwrite").option("overwriteSchema", "true").save(target)

    p = paths(pipeline, gold="gold_rate", name="bad_rate")
    with pytest.raises(ReconciliationError):
        run_reconciliation(spark, **p)
    report = read_report(p["report_json"])
    assert report["rc08.contract"]["status"] == "fail"
    assert report["rc08.contract"]["difference"] == 1.0


@pytest.mark.integration
def test_unlabelled_run_passes(spark, tmp_path):
    cols = [c for c in EXPECTED_RAW_COLUMNS if c != "Churn"]
    src = tmp_path / "unlabelled.csv"
    with open(src, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerow({**BASE, "customerID": "u1"})
        w.writerow({**BASE, "customerID": "u2", "tenure": "90", "Contract": "Two year"})
    load_bronze(spark, src, tmp_path / "bronze", batch_id="u")
    run_dq(spark, bronze_path=tmp_path / "bronze", results_path=tmp_path / "dq_results",
           report_json=tmp_path / "dq_report.json")
    write_silver(spark, bronze_path=tmp_path / "bronze", silver_path=tmp_path / "silver")
    write_gold(spark, silver_path=tmp_path / "silver", gold_dir=tmp_path / "gold")
    results = run_reconciliation(spark, csv_path=src, **paths(tmp_path, name="u"))
    assert sorted(r["check_id"] for r in results) == sorted(EXPECTED_IDS)
    assert all(r["status"] == "pass" for r in results), [r for r in results if r["status"] != "pass"]


def test_compute_source_stats_excel_bom(tmp_path):
    path = tmp_path / "bom.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # Excel "CSV UTF-8"
        w = csv.DictWriter(f, fieldnames=EXPECTED_RAW_COLUMNS)
        w.writeheader()
        w.writerow(BASE)
    assert compute_source_stats(path)["distinct_ids"] == 1
