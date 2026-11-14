"""Databricks job entry: the same src/pipeline.py as the laptop, writing Unity Catalog tables.

Run by the job in databricks.yml (serverless, environment version 4). Data lives in the Volume
/Volumes/<catalog>/<schema>/<volume> (raw CSV, JSON reports, model); Delta tables are managed
tables <catalog>.<schema>.<area>_<name>. See src/config/storage.py.
"""
import argparse
import json
import os
import sys

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

from src.ingestion.spark_session import get_spark  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402

runtime = f"databricks-serverless {os.environ.get('DATABRICKS_RUNTIME_VERSION')} -> {os.environ['CHURN_UC_SCHEMA']}"
summary = run_pipeline(get_spark(), sqlserver=False, runtime=runtime,
                       run_log=None, last_run="databricks_run.json")
for s in summary["stages"]:
    print(f"{s['stage']}: {s['seconds']}s {json.dumps(s.get('result'), default=str)}", flush=True)
print("RUN_SUMMARY " + json.dumps(summary, default=str))
