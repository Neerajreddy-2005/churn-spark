import json
from pathlib import Path

from src.config.settings import MODEL_METRICS_JSON, MODEL_SCORING_JSON, REPO_ROOT

INSIGHTS_PATH = REPO_ROOT / "docs" / "INSIGHTS.md"
START, END = "<!-- churn-model:start -->", "<!-- churn-model:end -->"


def _read(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def model_section(m, scoring=None):
    c, b = m["at_threshold"], m["baseline"]
    cap20, rule_cap = m["capture"]["top_20"], b["model_capture_at_same_flagged_share"]
    train, test = m["split"]["train"], m["split"]["test"]
    lines = [
        "## Churn model", "",
        f"Generated from `data/evidence/model_metrics.json` (run `{m['run_id']}`, {m['timestamp']}). "
        "Logistic regression on Silver features, stratified 80/20 split, seed "
        f"{m['seed']}. Training: {train['rows']:,} customers ({train['churn_rate_pct']}% churned). "
        f"Test: {test['rows']:,} customers ({test['churn_rate_pct']}% churned). All figures below are "
        "on the held-out test set unless stated.", "",
        f"- AUC-ROC {m['auc_roc_test']} (training {m['auc_roc_train']}); AUC-PR {m['auc_pr_test']}.",
        f"- At threshold {m['threshold']}: precision {c['precision']}, recall {c['recall']}, "
        f"F1 {c['f1']}, accuracy {c['accuracy']}.",
        f"- Capture rate: the riskiest 20% of customers contain {cap20['capture_rate_pct']}% of all churners "
        f"(lift {cap20['lift']}); a random 20% would contain 20%.",
        f"- Rule baseline `{b['rule']}`: precision {b['precision']}, recall {b['recall']}, F1 {b['f1']}; "
        f"it flags {b['flagged_share_pct']}% of customers. The model's riskiest {b['flagged_share_pct']}% "
        f"contain {rule_cap['capture_rate_pct']}% of churners, so with an unlimited contact list the "
        "model is only marginally better than the rule.",
        *([f"- With a limited contact budget, the case for the model: contacting the riskiest 20% "
           f"reaches {cap20['capture_rate_pct']}% of churners, against an expected "
           f"{b['rule_capture_at_budget_pct']['top_20']}% when using the rule (it cannot rank inside its "
           "flagged group, so contacts are picked at random among them)."]
          if "rule_capture_at_budget_pct" in b else []), "",
        "Top 5 drivers (standardised logistic coefficients; positive raises churn risk):", "",
        "| Feature | Coefficient |", "|---|---|",
        *[f"| {t['feature']} | {t['coefficient']} |" for t in m["top_coefficients"][:5]], "",
    ]
    if scoring and scoring.get("model_version") == m["run_id"]:  # ignore bands from an older model
        counts = scoring["band_counts"]
        lines += [
            f"Risk bands (high >= {scoring['high_cutoff']}, medium >= {scoring['medium_cutoff']}, else low), "
            f"all {scoring['customers_scored']:,} customers: high {counts['high']:,}, "
            f"medium {counts['medium']:,}, low {counts['low']:,}. "
            f"{scoring['in_training_set']:,} of them were in the training set (flagged `in_training_set`).", "",
        ]
    lines += [
        "Limitations: this is a single snapshot, so the model describes who is churned in one file, not "
        "who will churn next month. Scores for training-set customers are in-sample and look better than "
        "they would for new customers; use `in_training_set` to separate them. Coefficients show "
        "association, not cause: changing a customer's contract will not necessarily change their "
        "behaviour. The band cut-offs are business choices, not statistical results.", "",
    ]
    return "\n".join(lines)


def update_insights(path=INSIGHTS_PATH, metrics_path=MODEL_METRICS_JSON, scoring_path=MODEL_SCORING_JSON):
    """Replace (or append) the generated model section in INSIGHTS.md. No-op without metrics."""
    metrics = _read(metrics_path)
    out = Path(path)
    if metrics is None or not out.exists():
        return None
    text = out.read_text(encoding="utf-8")
    if START in text:
        text = text[:text.index(START)].rstrip() + "\n"
    section = f"{START}\n{model_section(metrics, _read(scoring_path))}\n{END}\n"
    out.write_text(text + "\n" + section, encoding="utf-8")
    return out
