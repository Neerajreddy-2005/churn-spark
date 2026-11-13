import json
import logging
from pathlib import Path

from pyspark.sql.types import DoubleType, LongType, StringType, StructField, StructType

logger = logging.getLogger(__name__)

RESULT_SCHEMA = StructType([
    StructField("run_id", StringType()),
    StructField("rule_id", StringType()),
    StructField("description", StringType()),
    StructField("columns", StringType()),
    StructField("checked_rows", LongType()),
    StructField("failing_rows", LongType()),
    StructField("failing_rate", DoubleType()),
    StructField("threshold", DoubleType()),
    StructField("status", StringType()),
    StructField("action", StringType()),
    StructField("run_ts", StringType()),
])


def write_results(spark, results, results_path, report_json):
    out = Path(results_path).resolve().as_posix()
    rows = [tuple(r[f.name] for f in RESULT_SCHEMA.fields) for r in results]
    # A few dozen rows: write one file instead of one per CPU core.
    df = spark.createDataFrame(rows, RESULT_SCHEMA).coalesce(1)
    df.write.format("delta").mode("append").save(out)

    summary = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "warn", "fail")}
    report = {
        "run_id": results[0]["run_id"] if results else None,
        "run_ts": results[0]["run_ts"] if results else None,
        "summary": summary,
        "results": results,
    }
    report_path = Path(report_json).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("DQ results written to %s and %s", out, report_path)
