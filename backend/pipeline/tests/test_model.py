import json

import pytest
from pyspark.sql import functions as F

from src.config.settings import RAW_CSV, SILVER_PATH
from src.model.features import (
    BOOLEAN, CATEGORICAL, EXCLUDED, FEATURES, FORBIDDEN, NUMERIC, build_pipeline, prepare,
    stratified_split,
)
from src.model.reasons import add_reasons, top_reasons
from src.model.score import band_counts, risk_band, score_customers, update_warehouse_scores, write_scores
from src.model.train import capture_stats, model_meta, train_and_evaluate
from src.warehouse.star import build_warehouse, month_key

SILVER_COLUMNS = (
    ["customer_id"] + [c for c in CATEGORICAL] + BOOLEAN + NUMERIC + ["churn_flag"]
)


def toy_row(customer_id, contract, churn, tenure):
    row = {c: "No" for c in CATEGORICAL}
    row.update(gender="Female", contract=contract, internet_service="DSL", payment_method="Mailed check")
    row.update({c: False for c in BOOLEAN})
    row.update(customer_id=customer_id, tenure_months=tenure, monthly_charges=50.0, churn_flag=churn)
    return tuple(row[c] for c in SILVER_COLUMNS)


def toy_df(spark, n=40):
    """Month-to-month customers churn, two-year customers do not; tenure is unrelated noise."""
    rows = []
    for i in range(n):
        churn = i % 2
        contract = "Month-to-month" if churn else "Two year"
        rows.append(toy_row(f"c{i:03d}", contract, churn, 1 + (i * 7) % 24))
    return spark.createDataFrame(rows, SILVER_COLUMNS)


# ---- unit ----

def test_features_exclude_forbidden_and_excluded_columns():
    assert not set(FEATURES) & set(FORBIDDEN)
    assert not set(FEATURES) & set(EXCLUDED)
    assert {"churn", "churn_flag", "customer_id", "total_charges_imputed", "_batch_id"} <= set(FORBIDDEN)


def test_prepare_keeps_only_id_label_and_features(spark):
    out = prepare(toy_df(spark))
    assert set(out.columns) == {"customer_id", "churn_flag", *FEATURES}
    assert dict(out.dtypes)["partner"] == "double"


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_stratified_split_keeps_classes_and_is_disjoint(spark):
    train, test = stratified_split(toy_df(spark, 200))
    for part in (train, test):
        assert part.filter("churn_flag = 0").count() > 0 and part.filter("churn_flag = 1").count() > 0
    train_ids = {r[0] for r in train.select("customer_id").collect()}
    test_ids = {r[0] for r in test.select("customer_id").collect()}
    assert not train_ids & test_ids
    assert len(train_ids) + len(test_ids) == 200


def test_risk_band_cutoffs(spark):
    scores = [0.0, 0.2999, 0.30, 0.5999, 0.60, 1.0]
    df = spark.createDataFrame([(s,) for s in scores], ["s"])
    bands = [r[0] for r in df.select(risk_band(F.col("s"))).collect()]
    assert bands == ["low", "low", "medium", "medium", "high", "high"]


def test_capture_stats_on_hand_made_ranking():
    # riskiest first: 10 customers, 4 churners, 3 of them in the top 3
    ranking = [1, 1, 1, 0, 0, 1, 0, 0, 0, 0]
    top30 = capture_stats(ranking, 30)
    assert (top30["customers"], top30["churners_captured"]) == (3, 3)
    assert top30["capture_rate_pct"] == 75.0 and top30["lift"] == 2.5
    assert capture_stats(ranking, 100)["capture_rate_pct"] == 100.0
    assert capture_stats([0, 0, 0], 50)["capture_rate_pct"] == 0.0


def test_top_reasons_only_positive_contributions():
    meta = {
        "slots": [
            {"feature": "contract", "kind": "category", "label": "Month-to-month"},
            {"feature": "contract", "kind": "category", "label": "Two year"},
            {"feature": "partner", "kind": "boolean"},
            {"feature": "tenure_months", "kind": "numeric"},
        ],
        "coefficients": [0.5, -0.5, -0.2, -0.8],
        "means": [0.5, 0.5, 1.0, 2.0],
        "std": [1.0, 1.0, 2.0, 12.0],
    }
    # month-to-month, no partner (x=0 < mean, negative coef -> positive), short tenure (x=0.1 < mean)
    reasons = top_reasons([1.0, 0.0, 0.0, 1 / 12], meta)
    assert reasons == ["tenure_months = 1 (low)", "contract = Month-to-month", "partner = No"]
    assert "contract = Two year" not in reasons
    # a long-tenure two-year customer with a partner has nothing pushing risk up: all None
    assert top_reasons([0.0, 1.0, 2.0, 5.0], meta) == [None, None, None]


def test_top_reasons_cut_long_names():
    """Audit #7: a 120-character column name used to be repeated in full in every scored row."""
    long_name, long_label = "x" * 120, "y" * 120
    meta = {"slots": [{"feature": long_name, "kind": "numeric"},
                      {"feature": long_name, "kind": "category", "label": long_label}],
            "coefficients": [1.0, 1.0], "means": [0.0, 0.0], "std": [1.0, 1.0]}
    numeric, category, _ = top_reasons([2.0, 1.0], meta)
    assert numeric == "x" * 39 + "… = 2 (high)"
    assert category == "x" * 39 + "… = " + "y" * 39 + "…"


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_reasons_on_toy_pipeline(spark):
    data = prepare(toy_df(spark))
    model = build_pipeline().fit(data)
    meta = model_meta(model, data, "toy")
    assert len(meta["coefficients"]) == len(meta["slots"]) == len(meta["means"]) == len(meta["std"])

    rows = add_reasons(model.transform(data), meta).select(
        "customer_id", "contract", "reason_1", "reason_2", "reason_3").collect()
    risky = [r for r in rows if r["contract"] == "Month-to-month"]
    safe = [r for r in rows if r["contract"] == "Two year"]
    assert all(r["reason_1"] == "contract = Month-to-month" for r in risky)
    # only positive contributions: the protective category is never given as a reason
    assert all("Two year" not in (t or "") for r in rows for t in (r["reason_1"], r["reason_2"], r["reason_3"]))
    assert all(r["reason_1"] != "contract = Month-to-month" for r in safe)
    # fewer than 3 positive contributions gives nulls, never padding
    assert any(r["reason_3"] is None for r in rows)


# ---- integration: real Silver ----

@pytest.fixture(scope="module")
def trained(spark, tmp_path_factory):
    base = tmp_path_factory.mktemp("model")
    metrics, metrics_path = train_and_evaluate(
        spark, SILVER_PATH, base / "churn_lr", base / "evidence" / "model_metrics.json", run_id="itest")
    return base, metrics, metrics_path


@pytest.mark.integration
@pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV missing")
def test_train_writes_complete_metrics(trained):
    base, metrics, path = trained
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    for key in ("run_id", "timestamp", "seed", "features", "split", "auc_roc_test", "auc_pr_test",
                "auc_roc_train", "at_threshold", "capture", "baseline", "top_coefficients", "model_path"):
        assert key in on_disk, key
    assert {"top_10", "top_20", "top_30"} == set(on_disk["capture"])
    assert {"tp", "fp", "tn", "fn"} == set(on_disk["at_threshold"]["confusion_matrix"])
    assert len(on_disk["top_coefficients"]) == 15
    # sanity only; the measured value is reported, not targeted
    print("measured test AUC-ROC:", on_disk["auc_roc_test"])
    assert 0.5 < on_disk["auc_roc_test"] < 1.0
    assert on_disk["split"]["train"]["rows"] + on_disk["split"]["test"]["rows"] == 7043
    assert (base / "churn_lr" / "model_meta.json").exists()


@pytest.mark.integration
@pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV missing")
def test_scores_and_warehouse_merge(spark, trained, tmp_path):
    base, _, _ = trained
    scores = score_customers(spark, base / "churn_lr", SILVER_PATH).cache()
    assert scores.count() == 7043
    assert scores.filter("risk_score IS NULL OR risk_score < 0 OR risk_score > 1 OR risk_band IS NULL").count() == 0
    high = scores.filter("risk_band = 'high'")
    assert high.count() > 0 and high.filter("reason_1 IS NULL").count() == 0
    assert scores.filter("in_training_set").count() == 5698
    assert sum(band_counts(scores).values()) == 7043

    write_scores(scores, tmp_path / "scores")
    wh = tmp_path / "wh"
    build_warehouse(spark, SILVER_PATH, wh, "2026-10", "itest")
    fact_path = (wh / "fact_customer_snapshot").resolve().as_posix()
    key = month_key("2026-10")
    before = spark.read.format("delta").load(fact_path).count()
    for _ in range(2):  # second run proves the MERGE is idempotent
        update_warehouse_scores(spark, scores, wh, "2026-10")
        fact = spark.read.format("delta").load(fact_path)
        assert fact.count() == before
        month = fact.filter(f"snapshot_date_key = {key}")
        assert month.filter("risk_score IS NULL OR risk_band IS NULL").count() == 0


def test_rule_capture_at_budget():
    from src.model.train import rule_capture_at_budget
    # rule flags 50% of customers and finds 80% of churners
    assert rule_capture_at_budget(0.8, 50.0, 25) == 40.0   # half of the flagged group, at random
    assert rule_capture_at_budget(0.8, 50.0, 50) == 80.0   # exactly the flagged group
    assert rule_capture_at_budget(0.8, 50.0, 75) == 90.0   # flagged group + half of the rest
