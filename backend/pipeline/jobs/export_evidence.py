"""Export the Databricks job definition, its recent run history and the last run summary into
data/evidence/ (committed). Emails, workspace URLs and ids that identify the account are removed.

Needs the Databricks CLI and DATABRICKS_HOST / DATABRICKS_TOKEN (backend/pipeline/.env.databricks).
Run from backend/pipeline:  python jobs/export_evidence.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))
from dotenv import load_dotenv  # noqa: E402

from src.config.settings import EVIDENCE_DIR  # noqa: E402

load_dotenv(PIPELINE / ".env.databricks")
CLI = shutil.which("databricks") or "databricks"
JOB_NAME = "churn-spark-pipeline"
VOLUME_SUMMARY = "dbfs:/Volumes/workspace/churn_spark/files/evidence/databricks_run.json"
# Fields that identify the person or workspace; dropped from the public evidence.
DROP = {"creator_user_name", "run_as_user_name", "run_page_url", "user_name", "owner_user_name",
        "effective_run_as", "run_as", "notebook_path", "git_source"}


def cli(*args):
    out = subprocess.run([CLI, *args, "-o", "json"], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in DROP}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"/(Workspace/)?Users/[^/]+", "/Users/<user>", value)
        value = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", value)
        return re.sub(r"https://[\w.-]+\.databricks\.com\S*", "<workspace-url>", value)
    return value


def main():
    job = next(j for j in cli("jobs", "list") if j["settings"]["name"] == JOB_NAME)
    definition = cli("jobs", "get", str(job["job_id"]))
    runs = cli("jobs", "list-runs", "--job-id", str(job["job_id"]), "--limit", "25")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "databricks_job_definition.json").write_text(
        json.dumps(scrub(definition["settings"]), indent=2), encoding="utf-8")
    keep = ("run_id", "run_name", "start_time", "end_time", "run_duration", "trigger", "state", "status")
    history = [{k: r.get(k) for k in keep if k in r} for r in runs]
    (EVIDENCE_DIR / "databricks_run_history.json").write_text(
        json.dumps(scrub(history), indent=2), encoding="utf-8")
    subprocess.run([CLI, "fs", "cp", VOLUME_SUMMARY, str(EVIDENCE_DIR / "databricks_run.json"), "--overwrite"],
                   check=True, capture_output=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    print(f"Exported definition, {len(history)} runs and the last run summary to {EVIDENCE_DIR}")


if __name__ == "__main__":
    main()
