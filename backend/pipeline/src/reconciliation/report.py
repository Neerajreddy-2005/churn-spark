import json
import logging
from pathlib import Path

from pyspark.sql.types import DoubleType, StringType, StructField, StructType

from src.config.storage import write_delta

logger = logging.getLogger(__name__)

RESULT_SCHEMA = StructType([
    StructField("run_id", StringType()),
    StructField("check_id", StringType()),
    StructField("description", StringType()),
    StructField("expected", StringType()),
    StructField("actual", StringType()),
    StructField("difference", DoubleType(), nullable=True),
    StructField("status", StringType()),
    StructField("run_ts", StringType()),
])


def write_results(spark, results, results_path, report_json):
    out = Path(results_path).resolve().as_posix()
    rows = [tuple(r[f.name] for f in RESULT_SCHEMA.fields) for r in results]
    df = spark.createDataFrame(rows, RESULT_SCHEMA).coalesce(1)
    write_delta(df, out, mode="append")

    summary = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "fail")}
    report = {
        "run_id": results[0]["run_id"] if results else None,
        "run_ts": results[0]["run_ts"] if results else None,
        "summary": summary,
        "results": results,
    }
    report_path = Path(report_json).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Reconciliation results written to %s and %s", out, report_path)
