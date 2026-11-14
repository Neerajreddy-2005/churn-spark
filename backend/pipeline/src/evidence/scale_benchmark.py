"""Scale benchmark: generate a synthetic dataset (src/evidence/scale_data.py) and run the full
pipeline on it, recording per-stage seconds and rows/second in data/evidence/scale_benchmark.json.

The run uses its own data root (keep it OUTSIDE OneDrive: gigabytes would sync), so the real
data, model and evidence are untouched. SQL Server is skipped (it measures the laptop's SQL
Express, not the pipeline).

    python -m src.evidence.scale_benchmark --rows 1000000 --root C:/Users/<you>/churn-spark-scale --driver-memory 6g
    python -m src.evidence.scale_benchmark --record-databricks <databricks_run.json> --rows 1000000

Results are "synthetic, resampled from 7,043 real rows".
"""
import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.config.settings import EVIDENCE_DIR, PROJECT_ROOT
from src.evidence.scale_data import generate

RESULTS = EVIDENCE_DIR / "scale_benchmark.json"
LABEL = "synthetic, resampled from 7,043 real rows (src/evidence/scale_data.py)"


def machine():
    info = {"os": platform.platform(), "python": platform.python_version(), "cpu": platform.processor()}
    if sys.platform == "win32":
        ps = ("$c=Get-CimInstance Win32_Processor; $s=Get-CimInstance Win32_ComputerSystem; "
              "\"$($c.Name)|$($c.NumberOfCores)|$($c.NumberOfLogicalProcessors)|$([math]::Round($s.TotalPhysicalMemory/1GB,1))\"")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
        name, cores, threads, ram = out.strip().split("|")
        info.update(cpu=name.strip(), cores=int(cores), threads=int(threads), ram_gb=float(ram))
    return info


def summarise(run, rows):
    stages = [{"stage": s["stage"], "status": s["status"], "seconds": s["seconds"],
               "rows_per_second": round(rows / s["seconds"]) if s["seconds"] else None}
              for s in run["stages"]]
    return {"rows": rows, "status": run["status"], "total_seconds": run["total_seconds"],
            "rows_per_second": round(rows / run["total_seconds"]) if run["total_seconds"] else None,
            "spark_version": run.get("spark_version"), "run_id": run["run_id"], "stages": stages}


def record(entry):
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {"label": LABEL, "runs": []}
    results["runs"] = [r for r in results["runs"] if (r["runtime"], r["rows"]) != (entry["runtime"], entry["rows"])]
    results["runs"].append(entry)
    results["runs"].sort(key=lambda r: (r["runtime"], r["rows"]))
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return RESULTS


def run_local(rows, root, driver_memory):
    data_root = Path(root) / f"{rows}"
    csv_path = data_root / "raw" / "Telco-Customer-Churn.csv"
    generated = generate(rows, csv_path)
    print(f"Generated {rows:,} rows in {generated['seconds']} s ({generated['bytes'] / 1e6:.0f} MB)", flush=True)

    env = {**os.environ, "CHURN_DATA_ROOT": str(data_root), "CHURN_SPARK_DRIVER_MEMORY": driver_memory}
    code = subprocess.run([sys.executable, "-m", "src.pipeline", "--no-sqlserver"], cwd=PROJECT_ROOT, env=env).returncode
    run = json.loads((data_root / "evidence" / "pipeline_run.json").read_text(encoding="utf-8"))
    entry = {"runtime": "laptop", "label": LABEL, "timestamp": datetime.now().astimezone().isoformat(),
             "machine": machine(), "driver_memory": driver_memory, "generate": generated,
             **summarise(run, rows)}
    entry["generate"].pop("path")
    print(f"Pipeline exit code {code}; recorded in {record(entry)}")
    return code


def record_databricks(path, rows):
    run = json.loads(Path(path).read_text(encoding="utf-8"))
    entry = {"runtime": "databricks-serverless", "label": LABEL, "timestamp": run.get("finished"),
             "machine": {"runtime": run.get("runtime")}, **summarise(run, rows)}
    print(f"Recorded in {record(entry)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scale benchmark on a synthetic dataset.")
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--root", help="data root for the synthetic run (outside OneDrive)")
    parser.add_argument("--driver-memory", default="6g")
    parser.add_argument("--record-databricks", help="path to a databricks_run.json from a scale run")
    args = parser.parse_args()
    if args.record_databricks:
        record_databricks(args.record_databricks, args.rows)
    else:
        if not args.root:
            parser.error("--root is required for a laptop run")
        sys.exit(run_local(args.rows, args.root, args.driver_memory))
