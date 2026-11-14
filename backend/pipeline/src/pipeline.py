"""Single entry point: bronze -> dq -> silver -> gold -> reconcile -> warehouse -> train -> score
(-> sqlserver on the laptop), with a per-stage run log.

    python -m src.pipeline                       # full run
    python -m src.pipeline --stage bronze        # one stage
    python -m src.pipeline --no-sqlserver        # skip the SQL Server copy

Every run appends one JSON line to data/evidence/pipeline_runs.jsonl (local history, git-ignored)
and rewrites data/evidence/pipeline_run.json (the latest run), including failed runs.
The Databricks job (jobs/run_pipeline.py) calls run_pipeline() with sqlserver=False.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime

from src.config.settings import EVIDENCE_DIR, MODEL_SCORES_PATH, SNAPSHOT_MONTH
from src.config.storage import read_delta
from src.ingestion.bronze_loader import load_bronze
from src.ingestion.spark_session import get_spark
from src.model.score import band_counts, score_customers, update_warehouse_scores, write_scores
from src.model.train import train_and_evaluate
from src.output.evidence import write_evidence
from src.quality.runner import run_dq
from src.reconciliation.checks import run_reconciliation
from src.transformations.gold import write_gold
from src.transformations.silver import write_silver
from src.warehouse.star import build_warehouse

logger = logging.getLogger(__name__)

STAGES = ["bronze", "dq", "silver", "gold", "reconcile", "warehouse", "train", "score", "sqlserver"]
RUN_LOG = EVIDENCE_DIR / "pipeline_runs.jsonl"
LAST_RUN = "pipeline_run.json"


def _status_counts(results, key="status"):
    counts = {}
    for r in results:
        counts[r[key]] = counts.get(r[key], 0) + 1
    return counts


def _stage_functions(spark, run_id, snapshot_month):
    def score():
        write_scores(score_customers(spark))
        scores = read_delta(spark, MODEL_SCORES_PATH)
        update_warehouse_scores(spark, scores, snapshot_month=snapshot_month)
        return {"customers_scored": scores.count(), "band_counts": band_counts(scores)}

    def train():
        metrics, _ = train_and_evaluate(spark, run_id=run_id)
        return {"auc_roc_test": metrics["auc_roc_test"], "auc_pr_test": metrics["auc_pr_test"]}

    def sqlserver():
        from src.output.sqlserver_writer import load_and_check  # needs the JDBC driver; laptop only
        loaded, results = load_and_check(spark, snapshot_month=snapshot_month, run_id=run_id,
                                         database=os.environ.get("SQLSERVER_DB", "churn_db"))
        return {"tables": {t: v["rows"] for t, v in loaded.items()}, "checks": _status_counts(results)}

    return {
        "bronze": lambda: {"rows": load_bronze(spark, batch_id=run_id)},
        "dq": lambda: _status_counts(run_dq(spark, run_id=run_id)),
        "silver": lambda: {"rows": write_silver(spark)},
        "gold": lambda: write_gold(spark),
        "reconcile": lambda: _status_counts(run_reconciliation(spark, run_id=run_id)),
        "warehouse": lambda: build_warehouse(spark, snapshot_month=snapshot_month, run_id=run_id),
        "train": train,
        "score": score,
        "sqlserver": sqlserver,
    }


def run_pipeline(spark, stages=None, snapshot_month=SNAPSHOT_MONTH, sqlserver=True, run_id=None,
                 runtime="laptop", run_log=RUN_LOG, last_run=LAST_RUN):
    """Run the stages in order; stop at the first failure. Returns the run summary (also on failure,
    after writing it, the original exception is re-raised)."""
    run_id = run_id or uuid.uuid4().hex
    stages = stages or [s for s in STAGES if sqlserver or s != "sqlserver"]
    functions = _stage_functions(spark, run_id, snapshot_month)
    summary = {"run_id": run_id, "runtime": runtime, "spark_version": spark.version,
               "snapshot_month": snapshot_month, "started": datetime.now().astimezone().isoformat(),
               "status": "running", "stages": []}
    total = time.perf_counter()
    error = None
    for name in stages:
        start = time.perf_counter()
        entry = {"stage": name}
        try:
            entry["result"] = functions[name]()
            entry["status"] = "ok"
        except Exception as e:  # recorded, then re-raised below
            entry["status"], entry["error"] = "failed", f"{type(e).__name__}: {str(e)[:300]}"
            error = e
        entry["seconds"] = round(time.perf_counter() - start, 1)
        summary["stages"].append(entry)
        logger.info("%s %s in %.1fs %s", name, entry["status"], entry["seconds"],
                    json.dumps(entry.get("result", entry.get("error")), default=str))
        if error:
            break
    summary["status"] = "failed" if error else "ok"
    summary["total_seconds"] = round(time.perf_counter() - total, 1)
    summary["finished"] = datetime.now().astimezone().isoformat()

    write_evidence(last_run, summary, EVIDENCE_DIR)
    if run_log:  # None on Databricks: Volume files are written whole, not appended to
        run_log.parent.mkdir(parents=True, exist_ok=True)
        with open(run_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, default=str) + "\n")
    if error:
        raise error
    return summary


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Run the churn pipeline end to end, or one stage.")
    parser.add_argument("--stage", choices=STAGES, help="run only this stage")
    parser.add_argument("--snapshot-month", default=SNAPSHOT_MONTH, help="yyyy-mm")
    parser.add_argument("--no-sqlserver", action="store_true", help="skip the SQL Server copy and its checks")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}", args.snapshot_month):
        parser.error("--snapshot-month must look like 2026-10")

    spark = get_spark()
    try:
        summary = run_pipeline(spark, stages=[args.stage] if args.stage else None,
                               snapshot_month=args.snapshot_month, sqlserver=not args.no_sqlserver)
    except Exception as e:
        print(f"Pipeline failed: {type(e).__name__}: {e}")
        return 1
    finally:
        spark.stop()
    for s in summary["stages"]:
        print(f"{s['stage']:<10}{s['status']:<7}{s['seconds']:>7.1f}s")
    print(f"Total seconds: {summary['total_seconds']}  (run log: {RUN_LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
