import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from pyspark.ml.evaluation import BinaryClassificationEvaluator
from pyspark.ml.stat import Summarizer
from pyspark.sql import functions as F

from src.config.settings import MODEL_DIR, MODEL_METRICS_JSON, SILVER_PATH
from src.ingestion.spark_session import get_spark
from src.model.features import (
    BOOLEAN, CATEGORICAL, EXCLUDED, FORBIDDEN, LABEL, NUMERIC, SEED, build_pipeline,
    churn_probability, feature_slots, prepare, slot_name, stratified_split,
)
from src.model.report import update_insights
from src.output.evidence import write_evidence

logger = logging.getLogger(__name__)

THRESHOLD = 0.5
CAPTURE_PCTS = (10, 20, 30)
META_FILE = "model_meta.json"
TRAIN_IDS = "train_customer_ids"


def capture_stats(labels_by_risk, pct):
    """labels_by_risk: 0/1 churn labels, riskiest customer first.
    Returns the share of all churners found in the top pct% (capture rate) and lift = capture / pct."""
    churners = sum(labels_by_risk)
    top = round(len(labels_by_risk) * pct / 100)
    captured = sum(labels_by_risk[:top])
    capture = 100 * captured / churners if churners else 0.0
    return {"top_pct": pct, "customers": top, "churners_captured": captured,
            "capture_rate_pct": round(capture, 2), "lift": round(capture / pct, 2)}


def rule_capture_at_budget(rule_recall, flagged_share_pct, pct):
    """Expected churner capture if the team can only contact pct% of customers and uses the yes/no
    rule: it cannot rank inside its flagged group, so it contacts flagged customers in random order
    (then unflagged ones, also at random, if the budget exceeds the flagged share)."""
    share = flagged_share_pct / 100
    budget = pct / 100
    if budget <= share:
        capture = rule_recall * budget / share
    else:
        capture = rule_recall + (1 - rule_recall) * (budget - share) / (1 - share)
    return round(100 * capture, 2)


def classification_metrics(df, prediction_col="prediction"):
    """Plain counts at the chosen threshold. df needs LABEL and a 0/1 prediction column."""
    def count(pred, label):
        return F.sum(((F.col(prediction_col) == pred) & (F.col(LABEL) == label)).cast("int"))

    row = df.agg(count(1, 1).alias("tp"), count(1, 0).alias("fp"),
                 count(0, 0).alias("tn"), count(0, 1).alias("fn")).first()
    tp, fp, tn, fn = (row[c] or 0 for c in ("tp", "fp", "tn", "fn"))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total = tp + fp + tn + fn
    return {
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
        "accuracy": round((tp + tn) / total, 4),
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "flagged_share_pct": round(100 * (tp + fp) / total, 2),
    }


def split_summary(df):
    row = df.agg(F.count("*").alias("n"), F.sum(LABEL).alias("churned")).first()
    return {"rows": row["n"], "churned": row["churned"],
            "churn_rate_pct": round(100 * row["churned"] / row["n"], 2)}


def _auc(evaluator, predictions, metric):
    return round(evaluator.setMetricName(metric).evaluate(predictions), 4)


def evaluate(model, train, test):
    """All metrics on the TEST set, plus train AUC to show the over/underfitting gap."""
    evaluator = BinaryClassificationEvaluator(labelCol=LABEL, rawPredictionCol="rawPrediction")
    scored_test = model.transform(test)
    scored_train = model.transform(train)

    ranked = (scored_test.select(LABEL, "customer_id", churn_probability().alias("p"))
              .orderBy(F.col("p").desc(), "customer_id"))
    labels = [r[LABEL] for r in ranked.collect()]  # test set only (20% of the data)
    capture = {f"top_{p}": capture_stats(labels, p) for p in CAPTURE_PCTS}

    rule = classification_metrics(
        test.withColumn("rule_pred", (F.col("contract") == "Month-to-month").cast("int")), "rule_pred")
    baseline = {
        "rule": "contract = Month-to-month",
        **rule,
        # The rule is a yes/no flag, not a ranking, so compare at the share of customers it flags.
        "model_capture_at_same_flagged_share": capture_stats(labels, rule["flagged_share_pct"]),
        # Fair comparison under a limited contact budget (expected value, random order inside the rule's flag).
        "rule_capture_at_budget_pct": {
            f"top_{p}": rule_capture_at_budget(rule["recall"], rule["flagged_share_pct"], p)
            for p in CAPTURE_PCTS},
        "random_ranking_capture_rate": "equals the top share by definition (top 20% finds 20% of churners)",
    }
    return {
        "auc_roc_test": _auc(evaluator, scored_test, "areaUnderROC"),
        "auc_pr_test": _auc(evaluator, scored_test, "areaUnderPR"),
        "auc_roc_train": _auc(evaluator, scored_train, "areaUnderROC"),
        "threshold": THRESHOLD,
        "at_threshold": classification_metrics(scored_test),
        "capture": capture,
        "baseline": baseline,
    }


def training_means(model, train):
    scaled = model.transform(train).select("scaled")
    return scaled.select(Summarizer.metrics("mean").summary(F.col("scaled")).alias("s")).first()["s"]["mean"].toArray()


def model_meta(model, train, run_id):
    scaler, lr = model.stages[-2], model.stages[-1]
    return {
        "run_id": run_id,
        "slots": feature_slots(model),
        "coefficients": lr.coefficients.toArray().tolist(),
        "intercept": lr.intercept,
        "means": training_means(model, train).tolist(),  # mean of the scaled vector over the TRAINING set
        "std": scaler.std.toArray().tolist(),
    }


def top_coefficients(meta, n=15):
    pairs = zip((slot_name(s) for s in meta["slots"]), meta["coefficients"])
    return [{"feature": name, "coefficient": round(c, 4)}
            for name, c in sorted(pairs, key=lambda p: -abs(p[1]))[:n]]


def save_model(model, meta, train, model_dir):
    path = Path(model_dir).resolve()
    model.write().overwrite().save(path.as_posix())
    (path / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # Remember who the model was fitted on, so scoring can flag in-sample customers.
    train.select("customer_id").write.mode("overwrite").parquet((path / TRAIN_IDS).as_posix())


def train_and_evaluate(spark, silver_path=SILVER_PATH, model_dir=MODEL_DIR,
                       metrics_path=MODEL_METRICS_JSON, run_id=None):
    run_id = run_id or uuid.uuid4().hex
    silver = spark.read.format("delta").load(Path(silver_path).resolve().as_posix())
    train, test = stratified_split(prepare(silver))
    train, test = train.cache(), test.cache()

    model = build_pipeline().fit(train)
    meta = model_meta(model, train, run_id)
    metrics = evaluate(model, train, test)
    save_model(model, meta, train, model_dir)

    payload = {
        "run_id": run_id,
        "timestamp": datetime.now().astimezone().isoformat(),
        "seed": SEED,
        "features": {"categorical": CATEGORICAL, "boolean": BOOLEAN, "numeric": NUMERIC,
                     "excluded": EXCLUDED, "never_features": FORBIDDEN},
        "split": {"train": split_summary(train), "test": split_summary(test)},
        "model": {"type": "LogisticRegression", "regParam": 0.01, "elasticNetParam": 0.0, "maxIter": 100},
        **metrics,
        "top_coefficients": top_coefficients(meta),
        "model_path": str(Path(model_dir).resolve()),
    }
    path = write_evidence(Path(metrics_path).name, payload, Path(metrics_path).parent)
    train.unpersist()
    test.unpersist()
    return payload, path


def _print(m):
    c, b = m["at_threshold"], m["baseline"]
    print(f"Train: {m['split']['train']}")
    print(f"Test:  {m['split']['test']}")
    print(f"AUC-ROC test {m['auc_roc_test']}  train {m['auc_roc_train']}  AUC-PR test {m['auc_pr_test']}")
    print(f"At threshold {m['threshold']}: precision {c['precision']} recall {c['recall']} "
          f"F1 {c['f1']} accuracy {c['accuracy']} {c['confusion_matrix']}")
    for k, v in m["capture"].items():
        print(f"Capture {k}: {v['capture_rate_pct']}% of churners (lift {v['lift']}), random = {v['top_pct']}%")
    print(f"Baseline {b['rule']}: precision {b['precision']} recall {b['recall']} F1 {b['f1']} "
          f"flags {b['flagged_share_pct']}% of customers; model capture at that share "
          f"{b['model_capture_at_same_flagged_share']['capture_rate_pct']}%")
    for k, v in b["rule_capture_at_budget_pct"].items():
        print(f"Budget {k}: model {m['capture'][k]['capture_rate_pct']}% vs rule (expected) {v}% of churners")
    print("Top coefficients:")
    for t in m["top_coefficients"][:10]:
        print(f"  {t['coefficient']:>8}  {t['feature']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start = time.perf_counter()
    spark = get_spark()
    try:
        metrics, path = train_and_evaluate(spark)
        update_insights()
        _print(metrics)
        print(f"Wrote {path}")
        print(f"Model saved to {metrics['model_path']}")
        print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
    finally:
        spark.stop()
