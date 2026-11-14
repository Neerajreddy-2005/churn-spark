"""Databricks job entry: the same src/ stages as the laptop, writing Unity Catalog tables.

Run by the job in databricks.yml (serverless, environment version 4). Data lives in the Volume
/Volumes/<catalog>/<schema>/<volume> (raw CSV, JSON reports, model); Delta tables are managed
tables <catalog>.<schema>.<area>_<name>. See src/config/storage.py.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", default="workspace")
parser.add_argument("--schema", default="churn_spark")
parser.add_argument("--volume", default="files")
# Folder holding src/ (the bundle passes ${workspace.file_path}). Serverless runs this file via
# exec(), so __file__ is not defined there and the working directory is not guaranteed.
parser.add_argument("--code-root", required=True)
args = parser.parse_args()

# Must be set before src.config.settings is imported: it reads them at import time.
os.environ["CHURN_DATA_ROOT"] = f"/Volumes/{args.catalog}/{args.schema}/{args.volume}"
os.environ["CHURN_UC_SCHEMA"] = f"{args.catalog}.{args.schema}"
sys.path.insert(0, args.code_root)  # backend/pipeline, so "import src" works

from src.config.settings import EVIDENCE_DIR, MODEL_SCORES_PATH, SNAPSHOT_MONTH  # noqa: E402
from src.config.storage import read_delta  # noqa: E402
from src.ingestion.bronze_loader import load_bronze  # noqa: E402
from src.ingestion.spark_session import get_spark  # noqa: E402
from src.model.score import band_counts, score_customers, update_warehouse_scores, write_scores  # noqa: E402
from src.model.train import train_and_evaluate  # noqa: E402
from src.output.evidence import write_evidence  # noqa: E402
from src.quality.runner import run_dq  # noqa: E402
from src.reconciliation.checks import run_reconciliation  # noqa: E402
from src.transformations.gold import write_gold  # noqa: E402
from src.transformations.silver import write_silver  # noqa: E402
from src.warehouse.star import build_warehouse  # noqa: E402

run_id = uuid.uuid4().hex
spark = get_spark()
stages = []


def stage(name, fn):
    start = time.perf_counter()
    result = fn()
    seconds = round(time.perf_counter() - start, 1)
    stages.append({"stage": name, "seconds": seconds, "result": result})
    print(f"{name}: {seconds}s {json.dumps(result, default=str)}", flush=True)
    return result


def dq():
    results = run_dq(spark, run_id=run_id)
    return {s: sum(r["status"] == s for r in results) for s in ("pass", "warn", "fail")}


def recon():
    results = run_reconciliation(spark, run_id=run_id)
    return {s: sum(r["status"] == s for r in results) for s in ("pass", "fail")}


def train():
    metrics, _ = train_and_evaluate(spark, run_id=run_id)
    return {"auc_roc_test": metrics["auc_roc_test"], "auc_pr_test": metrics["auc_pr_test"]}


def score():
    write_scores(score_customers(spark))
    scores = read_delta(spark, MODEL_SCORES_PATH)
    update_warehouse_scores(spark, scores)
    return {"customers_scored": scores.count(), "band_counts": band_counts(scores)}


total_start = time.perf_counter()
stage("bronze", lambda: {"rows": load_bronze(spark, batch_id=run_id)})
stage("data_quality", dq)
stage("silver", lambda: {"rows": write_silver(spark)})
stage("gold", lambda: write_gold(spark))
stage("reconciliation", recon)
stage("warehouse", lambda: build_warehouse(spark, run_id=run_id))
stage("train", train)
stage("score", score)

summary = {
    "run_id": run_id,
    "runtime": "databricks-serverless",
    "databricks_runtime_version": os.environ.get("DATABRICKS_RUNTIME_VERSION"),
    "spark_version": spark.version,
    "uc_schema": os.environ["CHURN_UC_SCHEMA"],
    "snapshot_month": SNAPSHOT_MONTH,
    "timestamp": datetime.now().astimezone().isoformat(),
    "total_seconds": round(time.perf_counter() - total_start, 1),
    "stages": stages,
}
path = write_evidence("databricks_run.json", summary, EVIDENCE_DIR)
print(f"Wrote {path}")
print("RUN_SUMMARY " + json.dumps(summary, default=str))
