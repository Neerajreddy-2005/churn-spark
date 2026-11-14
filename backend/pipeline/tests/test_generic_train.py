"""Mode 2, step 2: training, leakage guard, scores, insights and reconciliation (synthetic data)."""
import random

import pytest

from src.generic.checks import can_train, run_checks
from src.generic.profile import profile_table
from src.generic.reader import write_canonical
from src.generic.suggest import suggest
from src.generic.train import TrainingError, train_dataset

HEADER = ["CustomerID", "Plan", "Calls", "Spend", "Account Status", "Churn"]


def make_rows(n=600, seed=7):
    """Churn depends on plan and calls; 'Account Status' copies the outcome (a leak with an innocent name)."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        plan = rng.choice(["Basic", "Pro", "Max"])
        calls = rng.randint(0, 10)
        p = 0.1 + (0.35 if plan == "Basic" else 0) + 0.04 * calls
        churn = rng.random() < p
        calls_txt = "" if i % 25 == 0 else str(calls)  # some blanks -> imputed + missing flag
        rows.append([f"C{i:04d}", plan, calls_txt, f"{rng.uniform(10, 90):.2f}",
                     "Closed" if churn else "Open", "Yes" if churn else "No"])
    return rows


@pytest.fixture(scope="module")
def trained(spark, tmp_path_factory):
    d = tmp_path_factory.mktemp("gen")
    cols = write_canonical(HEADER, make_rows(), d / "c.csv")
    prof = profile_table(spark, d / "c.csv", cols)
    s = suggest(prof)
    review = {"id": s["id"], "target": s["target"], "positive": s["positive"], "features": s["features"]}
    assert can_train(run_checks(prof, review))
    result, scores = train_dataset(spark, d / "c.csv", prof, review)
    return result, scores.collect(), s


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_leak_with_innocent_name_is_removed(trained):
    result, _, s = trained
    assert "c4" in s["features"]  # the name rule cannot see it ...
    assert [x["name"] for x in result["leakage"]["removed"]] == ["Account Status"]  # ... the AUC guard does
    assert result["leakage"]["removed"][0]["single_column_auc"] == 1.0
    assert "Account Status" not in result["features"]["text"]


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_metrics_are_honest(trained):
    result, _, _ = trained
    m = result["metrics"]
    assert 0.6 < m["auc_roc_test"] < 0.99 and not result["suspicious"]
    assert result["rows"]["train"] + result["rows"]["test"] == result["rows"]["total"] == 600
    top20 = m["capture"]["top_20"]
    assert top20["capture_rate_pct"] <= top20["max_possible_pct"] <= 100
    assert result["features"]["missing_flags"] == ["Calls"]


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_scores_reasons_and_reconciliation(trained):
    result, scores, _ = trained
    assert len(scores) == 600 and len({r["row_id"] for r in scores}) == 600
    assert all(r["risk_band"] in ("high", "medium", "low") for r in scores)
    riskiest = max(scores, key=lambda r: r["risk_score"])
    assert riskiest["reason_1"] and not any("Account Status" in (riskiest[f"reason_{i}"] or "") for i in (1, 2, 3))
    assert sum(r["in_training_set"] for r in scores) == result["rows"]["train"]
    assert result["reconciled"] and all(r["status"] == "pass" for r in result["reconciliation"])


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_drivers_and_insights(trained):
    result, _, _ = trained
    feats = [d["feature"] for d in result["drivers"]]
    assert len(feats) == len(set(feats))  # one entry per feature
    assert feats[0] in ("Plan", "Calls")
    top = result["insights"][0]
    assert top["name"] in ("Plan", "Calls") and top["spread_pct_points"] > 10
    assert all(sum(s["customers"] for s in f["segments"]) == 600 for f in result["insights"])


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_one_class_holdout_is_refused(spark, tmp_path):
    rows = [[f"C{i}", "Basic", str(i % 5), "Yes" if i < 3 else "No"] for i in range(300)]
    cols = write_canonical(["CustomerID", "Plan", "Calls", "Churn"], rows, tmp_path / "c.csv")
    prof = profile_table(spark, tmp_path / "c.csv", cols)
    s = suggest(prof)
    review = {"id": s["id"], "target": s["target"], "positive": s["positive"], "features": s["features"]}
    assert not can_train(run_checks(prof, review))  # g05: only 3 churners
    with pytest.raises(TrainingError):
        train_dataset(spark, tmp_path / "c.csv", prof, review)


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_insight_labels_are_readable(trained):
    result, _, _ = trained
    calls = next(f for f in result["insights"] if f["name"] == "Calls")
    # 0..10 calls: one group per value (plus the blanks), not "0 to 1.10" style ranges
    assert [s["segment"] for s in calls["segments"]] == [str(i) for i in range(11)] + ["(missing)"]
    from src.generic.train import _bin_label, _fmt
    assert _fmt(250898) == "250.9k" and _fmt(1_500_000) == "1.5M" and _fmt(12.345) == "12.35"
    assert _bin_label({"from": 18, "to": 25.4}, False, True) == "18 to 25"
    assert _bin_label({"from": 25.4, "to": 32.8}, False, True) == "26 to 32"
    assert _bin_label({"from": 84.6, "to": 92}, True, True) == "85 to 92"


@pytest.mark.integration  # writes Delta / trains a model: slow on a laptop
def test_reasons_read_as_words(spark, tmp_path):
    rng = random.Random(3)
    rows = []
    for i in range(500):
        complain = rng.random() < 0.3
        calls = "" if i % 6 == 0 else str(rng.randint(0, 5))
        churn = rng.random() < (0.15 + (0.5 if complain else 0) + (0.2 if calls == "" else 0))
        rows.append([f"C{i}", "1" if complain else "0", calls, "Yes" if churn else "No"])
    cols = write_canonical(["CustomerID", "Complain", "Calls", "Churn"], rows, tmp_path / "c.csv")
    prof = profile_table(spark, tmp_path / "c.csv", cols)
    s = suggest(prof)
    review = {"id": s["id"], "target": s["target"], "positive": s["positive"], "features": s["features"]}
    _, scores = train_dataset(spark, tmp_path / "c.csv", prof, review)
    texts = {r for x in scores.collect() for r in (x["reason_1"], x["reason_2"], x["reason_3"]) if r}
    assert "Complain = Yes" in texts and not any(t.startswith("Complain = 1") for t in texts)
    assert "Calls is missing" in texts and not any("missing = " in t for t in texts)
