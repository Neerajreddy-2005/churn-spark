"""Evidence for Mode 2: run real public churn datasets through read -> profile -> suggest -> checks -> train.

The datasets are third-party Kaggle files kept locally (git-ignored); only aggregate results are recorded:
a SHA-256 of each file, row/column counts, the suggestions, checks, holdout metrics and leakage findings.
One extra run puts the IBM file's outcome-named columns back in, to show the AUC leakage guard on its own.

    python -m src.evidence.mode2_datasets <file> [<file> ...]
"""
import hashlib
import json
import logging
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from src.generic.checks import can_train, run_checks
from src.generic.profile import profile_table
from src.generic.reader import read_table, write_canonical
from src.generic.suggest import suggest
from src.generic.train import train_dataset
from src.ingestion.spark_session import get_spark
from src.output.evidence import write_evidence

EVIDENCE_NAME = "mode2_datasets.json"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_one(spark, path, work, override_leakage=False):
    t0 = time.perf_counter()
    header, rows, info = read_table(path)
    canon = Path(work) / f"{Path(path).stem}{'-override' if override_leakage else ''}.csv"
    cols = write_canonical(header, rows, canon)
    prof = profile_table(spark, canon, cols)
    s = suggest(prof)
    names = {c["key"]: c["name"] for c in cols}
    feats = list(s["features"])
    if override_leakage:  # a user ticking the outcome-named columns back on
        feats += [k for k, e in s["exclude"].items() if e["kind"] == "leakage"]
    out = {
        "file": Path(path).name, "sha256": _sha256(path),
        "variant": "leakage columns put back" if override_leakage else "suggested columns",
        "rows": prof["rows"], "columns": len(cols), "sheet": info.get("sheet"),
        "suggested": {"target": names.get(s["target"]), "positive": s["positive"], "id": names.get(s["id"]),
                      "left_out": {names[k]: e["kind"] for k, e in s["exclude"].items()},
                      "is_churn_data": s["is_churn_data"]},
    }
    if not s["is_churn_data"]:
        out.update(outcome="exploration only (no churn column)", seconds=round(time.perf_counter() - t0, 1))
        return out
    review = {"id": s["id"], "target": s["target"], "positive": s["positive"], "features": feats}
    checks = run_checks(prof, review)
    out["checks"] = {c["rule_id"]: c["status"] for c in checks}
    if not can_train(checks):
        out.update(outcome="not trained (checks failed)", seconds=round(time.perf_counter() - t0, 1))
        return out
    res, scores = train_dataset(spark, canon, prof, review)
    bands = {r["risk_band"]: r["count"] for r in scores.groupBy("risk_band").count().collect()}
    scores.unpersist()
    m = res["metrics"]
    out.update(
        outcome="trained", rows_split=res["rows"], base_churn_rate_pct=res["base_churn_rate_pct"],
        auc_roc_test=m["auc_roc_test"], auc_roc_train=m["auc_roc_train"], auc_pr_test=m["auc_pr_test"],
        top20_capture_pct=m["capture"]["top_20"]["capture_rate_pct"],
        top20_max_possible_pct=m["capture"]["top_20"]["max_possible_pct"],
        suspicious=res["suspicious"],
        leakage_removed=[(x["name"], x["single_column_auc"]) for x in res["leakage"]["removed"]],
        leakage_warned=[(x["name"], x["single_column_auc"]) for x in res["leakage"]["warned"]],
        top_drivers=[d["feature"] for d in res["drivers"][:5]],
        reconciliation={"passed": sum(r["status"] == "pass" for r in res["reconciliation"]),
                        "checks": len(res["reconciliation"])},
        risk_bands=bands, seconds=round(time.perf_counter() - t0, 1),
    )
    return out


def main(files):
    spark = get_spark("mode2-evidence")
    results = []
    try:
        with tempfile.TemporaryDirectory() as work:
            for f in files:
                results.append(run_one(spark, f, work))
                if results[-1]["suggested"]["left_out"] and "leakage" in results[-1]["suggested"]["left_out"].values():
                    results.append(run_one(spark, f, work, override_leakage=True))
    finally:
        spark.stop()
    path = write_evidence(EVIDENCE_NAME, {
        "timestamp": datetime.now().astimezone().isoformat(),
        "command": "python -m src.evidence.mode2_datasets <files>",
        "note": "Public Kaggle files (not committed). Each split is stratified 80/20 with seed 42; metrics are on the 20% holdout.",
        "results": results,
    })
    for r in results:
        print(f"{r['file']:40} {r['variant']:26} {r.get('outcome'):40} AUC {r.get('auc_roc_test')}  "
              f"recon {r.get('reconciliation')}  leakage removed {r.get('leakage_removed')}")
    print(f"Evidence: {path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
