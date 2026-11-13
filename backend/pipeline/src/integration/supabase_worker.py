"""Laptop worker: queued Supabase uploads -> full pipeline -> results back to Supabase.

Claims, status updates and small writes go through JDBC as postgres (session pooler); the big
DataFrames are written with Spark JDBC. Spark JDBC inserts are NOT one transaction, so an upload
stays `processing` until every write has succeeded; any error turns it into `failed`. A re-processed
(org, month) deletes its old customer_scores and segment_aggregates first, so retries are idempotent.
"""
import argparse
import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import shutil

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from src.config.settings import (
    EVIDENCE_DIR, GOLD_OVERALL, GOLD_SEGMENTS, MODEL_DIR, PROJECT_ROOT, REQUIRED_RAW_COLUMNS,
)
from src.config.supabase import jdbc_props, jdbc_url, redact, service_key
from src.ingestion.bronze_loader import ColumnMismatch, load_bronze
from src.ingestion.spark_session import get_spark
from src.integration import supabase_api as api
from src.integration.supabase_migrate import connect, execute, query
from src.model.score import score_customers
from src.model.train import META_FILE
from src.quality import report as dq_report_schema
from src.quality.runner import DataQualityError, run_dq
from src.reconciliation import report as recon_report_schema
from src.reconciliation.checks import ReconciliationError, run_reconciliation
from src.transformations.gold import write_gold
from src.transformations.silver import write_silver

logger = logging.getLogger(__name__)

BUCKET = "uploads"
MAX_BYTES = 5 * 1024 * 1024
STUCK_MINUTES = 60
REASON_LIMIT = 500
UPLOADS_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOAD_SUFFIXES = (".csv", ".xlsx")
RUNS_DIR = PROJECT_ROOT / "data" / "runs"
WORKER_EVIDENCE = EVIDENCE_DIR / "worker_runs.jsonl"
SEGMENT_TYPES = ["overall", *GOLD_SEGMENTS.values()]


class Rejected(Exception):
    """The upload itself is unacceptable (bad path, size, columns, data quality)."""


# ---- small pure helpers ----

def validate_upload(org_id, file_path, size_bytes):
    if not file_path.startswith(f"{org_id}/"):
        raise Rejected("file path is not inside the organisation's folder")
    if ".." in file_path.split("/") or "\\" in file_path:
        raise Rejected("file path is not allowed")
    if not 0 < int(size_bytes) <= MAX_BYTES:
        raise Rejected("file is larger than 5 MB")
    if Path(file_path).suffix.lower() not in UPLOAD_SUFFIXES:
        raise Rejected("only .csv and .xlsx files are accepted")


def format_reason(text):
    """Redacted, single-line, at most 500 characters."""
    text = " ".join(redact(text).split())
    return text if len(text) <= REASON_LIMIT else text[:REASON_LIMIT - 3] + "..."


def summarize(results, statuses):
    return {s: sum(1 for r in results if r["status"] == s) for s in statuses}


def column_reason(missing, extra):
    """Plain-language rejection for a file whose columns do not match the Telco layout."""
    def some(cols, n=4):
        return ", ".join(cols[:n]) + (f" and {len(cols) - n} more" if len(cols) > n else "")
    if len(missing) > len(REQUIRED_RAW_COLUMNS) // 2:
        head = (f"This file does not look like the Telco churn layout: {len(missing)} of the "
                f"{len(REQUIRED_RAW_COLUMNS)} required columns are missing ({some(missing)}).")
    else:
        head = f"Missing columns: {some(missing, 10)}." if missing else ""
    parts = [head] if head else []
    if len(extra) == 1 and ";" in extra[0]:  # whole header read as one column
        parts.append("The file looks separated by semicolons; save it as comma-separated CSV.")
    elif extra:
        parts.append(f"Columns that are not part of the layout: {some(extra)}.")
    lowered = {c.lower() for c in extra}
    if any(c.lower() in lowered for c in missing):
        parts.append("Column names are case-sensitive.")
    parts.append("Churn is optional; every other column is required, with no extra columns.")
    return " ".join(parts)


def dq_reason(results):
    """Rejection text naming each failed rule with its description and failing row count."""
    failed = [r for r in results if r["status"] == "fail"]
    # All ids first: the stored reason is capped at REASON_LIMIT, so details may be cut, ids never.
    return (f"Data quality failed: {', '.join(r['rule_id'] for r in failed)}. Details: " + "; ".join(
        f"{r['rule_id']} {r['description']} ({r['failing_rows']} rows)" for r in failed))


@contextmanager
def _timed(stages, name):
    start = time.perf_counter()
    try:
        yield
    finally:
        stages[name] = round(stages.get(name, 0) + time.perf_counter() - start, 2)


@contextmanager
def _db(spark):
    conn = connect(spark)
    try:
        yield conn
    finally:
        conn.close()


def _write_url():
    # Spark sends strings as varchar; stringtype=unspecified lets Postgres cast them to uuid/timestamptz.
    return jdbc_url() + "&stringtype=unspecified"


def _jdbc_write(df, table):
    df.write.jdbc(url=_write_url(), table=f"public.{table}", mode="append", properties=jdbc_props())


# ---- Supabase writes ----

def claim_next(conn, org_id=None):
    """Atomically take the oldest queued upload (skip locked, so two workers never share one)."""
    sql = ("update public.uploads set status = 'processing', started_at = now() where id = ("
           " select id from public.uploads where status = 'queued'"
           + (" and org_id = ?::uuid" if org_id else "") +
           " order by created_at limit 1 for update skip locked)"
           " returning id::text, org_id::text, file_path, original_name, size_bytes::text,"
           " snapshot_month, uploaded_by::text")
    rows = query(conn, sql, (org_id,) if org_id else ())
    if not rows:
        return None
    keys = ("id", "org_id", "file_path", "original_name", "size_bytes", "snapshot_month", "uploaded_by")
    return dict(zip(keys, rows[0]))


def fail_stuck(conn, org_id=None):
    sql = ("update public.uploads set status = 'failed', finished_at = now(),"
           " status_reason = 'worker interrupted; please re-upload'"
           f" where status = 'processing' and started_at < now() - interval '{STUCK_MINUTES} minutes'"
           + (" and org_id = ?::uuid" if org_id else "") + " returning id::text")
    rows = query(conn, sql, (org_id,) if org_id else ())
    if rows:
        logger.warning("Marked %d stuck upload(s) as failed", len(rows))
    return len(rows)


def set_final_status(conn, upload_id, status, reason, run_id):
    execute(conn, "update public.uploads set status = ?, status_reason = ?, run_id = ?,"
                  " finished_at = now() where id = ?::uuid",
            (status, None if reason is None else format_reason(reason), run_id, upload_id))


def write_check_results(spark, results, schema, table, org_id):
    """dq_results / reconciliation_results: same columns as the Delta result tables plus org_id."""
    full = StructType([*schema.fields, StructField("org_id", StringType())])
    rows = [(*(r[f.name] for f in schema.fields), org_id) for r in results]
    _jdbc_write(spark.createDataFrame(rows, full).coalesce(1), table)


def segment_aggregate_rows(silver, gold, org_id, month):
    """One row per (segment_type, segment). Counts and rates come from Gold; money from Silver."""
    d2 = "decimal(12,2)"
    charges = F.col("monthly_charges").cast(d2)
    aggs = [F.coalesce(F.sum(charges), F.lit(0).cast(d2)).alias("mrr"),
            F.coalesce(F.sum(F.when(F.col("churn"), charges)), F.lit(0).cast(d2)).alias("mrr_lost")]
    money = {"overall": {"All": silver.agg(*aggs).collect()[0]}}
    for column in GOLD_SEGMENTS.values():
        grouped = silver.groupBy(F.col(column).cast("string").alias("segment")).agg(*aggs)
        money[column] = {r["segment"]: r for r in grouped.collect()}

    labelled = gold[GOLD_OVERALL].first()["churned"] is not None  # no Churn column -> loss unknown
    out = []
    for stype, table in [("overall", GOLD_OVERALL), *((c, t) for t, c in GOLD_SEGMENTS.items())]:
        for g in gold[table].collect():
            m = money[stype][g["segment"]]
            out.append({
                "org_id": org_id, "snapshot_month": month, "segment_type": stype,
                "segment": g["segment"], "customers": g["customers"], "churned": g["churned"],
                "churn_rate_pct": None if g["churn_rate_pct"] is None else Decimal(str(g["churn_rate_pct"])),
                "mrr": m["mrr"], "mrr_lost": m["mrr_lost"] if labelled else None,
            })
    return out


def replace_segment_aggregates(conn, org_id, month, rows):
    """Delete then insert in one transaction (small table)."""
    conn.setAutoCommit(False)
    try:
        execute(conn, "delete from public.segment_aggregates where org_id = ?::uuid and snapshot_month = ?",
                (org_id, month))
        for r in rows:
            execute(conn, "insert into public.segment_aggregates (org_id, snapshot_month, segment_type,"
                          " segment, customers, churned, churn_rate_pct, mrr, mrr_lost)"
                          " values (?::uuid, ?, ?, ?, ?::bigint, ?::bigint, ?::numeric, ?::numeric, ?::numeric)",
                    (r["org_id"], r["snapshot_month"], r["segment_type"], r["segment"], r["customers"],
                     r["churned"], r["churn_rate_pct"], r["mrr"], r["mrr_lost"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.setAutoCommit(True)


def score_table(spark, silver, org_id, month, silver_path):
    scores = score_customers(spark, silver_path=silver_path)
    cols = silver.select("customer_id", "contract", "internet_service", "payment_method", "tenure_months",
                         "tenure_band", F.col("monthly_charges").cast("decimal(8,2)").alias("monthly_charges"),
                         F.col("churn").alias("churned"))
    return scores.join(cols, "customer_id").select(
        F.lit(org_id).alias("org_id"), F.lit(month).alias("snapshot_month"), "customer_id",
        "risk_score", "risk_band", "reason_1", "reason_2", "reason_3", "contract", "internet_service",
        "payment_method", "tenure_months", "tenure_band", "monthly_charges", "churned",
        "model_version", "scored_at")


def record_run(conn, run, org_id, upload):
    execute(conn,
            "insert into public.pipeline_runs (run_id, org_id, upload_id, snapshot_month, status, started_at,"
            " finished_at, stage_seconds, rows, dq_summary, recon_summary, model_version)"
            " values (?, ?::uuid, ?::uuid, ?, ?, ?::timestamptz, ?::timestamptz, ?::jsonb, ?::jsonb, ?::jsonb,"
            " ?::jsonb, ?)",
            (run["run_id"], org_id, upload["id"], upload["snapshot_month"], run["status"], run["started_at"],
             run["finished_at"], json.dumps(run["stage_seconds"]), json.dumps(run["rows"]),
             json.dumps(run["dq_summary"]), json.dumps(run["recon_summary"]), run["model_version"]))


# ---- pipeline ----

def download(upload, dest_dir):
    status, data = api.storage_download_bytes(BUCKET, upload["file_path"], service_key())
    if status != 200 or not isinstance(data, bytes):
        raise RuntimeError(f"could not download the uploaded file (HTTP {status})")
    if len(data) > MAX_BYTES:
        raise Rejected("file is larger than 5 MB")
    dest = Path(dest_dir) / ("input" + Path(upload["file_path"]).suffix.lower())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _checked(spark, org_id, run, stage, report_json, schema, table, run_fn):
    """Run a checking stage, store its results in Supabase (even on failure), then re-raise its error."""
    error = None
    with _timed(run["stage_seconds"], stage):
        try:
            run_fn()
        except (DataQualityError, ReconciliationError) as e:
            error = e
    results = json.loads(Path(report_json).read_text(encoding="utf-8"))["results"]
    run["rows"][table] = len(results)
    with _timed(run["stage_seconds"], "supabase_write"):
        write_check_results(spark, results, schema, table, org_id)
    return results, error


def run_pipeline(spark, upload, csv_path, run, run_dir):
    org_id, month, stages, rows = upload["org_id"], upload["snapshot_month"], run["stage_seconds"], run["rows"]
    bronze, silver_path, gold_dir = run_dir / "bronze", run_dir / "silver", run_dir / "gold"
    dq_json = run_dir / "quality" / "dq_report.json"
    recon_json = run_dir / "reconciliation" / "reconciliation_report.json"

    with _timed(stages, "bronze"):
        try:
            rows["bronze"] = load_bronze(spark, csv_path, bronze_path=bronze, batch_id=run["run_id"])
        except ColumnMismatch as e:
            raise Rejected(column_reason(e.missing, e.extra)) from None

    results, error = _checked(
        spark, org_id, run, "dq", dq_json, dq_report_schema.RESULT_SCHEMA, "dq_results",
        lambda: run_dq(spark, bronze_path=bronze, results_path=run_dir / "quality" / "dq_results",
                       report_json=dq_json, run_id=run["run_id"]))
    run["dq_summary"] = summarize(results, ("pass", "warn", "fail"))
    if error:
        raise Rejected(dq_reason(results))

    with _timed(stages, "silver"):
        rows["silver"] = write_silver(spark, bronze_path=bronze, silver_path=silver_path)
    with _timed(stages, "gold"):
        rows["gold"] = sum(write_gold(spark, silver_path=silver_path, gold_dir=gold_dir).values())

    results, error = _checked(
        spark, org_id, run, "reconciliation", recon_json, recon_report_schema.RESULT_SCHEMA,
        "reconciliation_results",
        lambda: run_reconciliation(
            spark, csv_path=csv_path, bronze_path=bronze, silver_path=silver_path, gold_dir=gold_dir,
            dq_report_json=dq_json, results_path=run_dir / "reconciliation" / "reconciliation_results",
            report_json=recon_json, run_id=run["run_id"]))
    run["recon_summary"] = summarize(results, ("pass", "fail"))
    if error:
        raise error

    silver = spark.read.format("delta").load(silver_path.resolve().as_posix())
    table = score_table(spark, silver, org_id, month, silver_path).cache()
    try:
        with _timed(stages, "score"):  # runs the model once; the write reuses the cache
            rows["customer_scores"] = table.count()
            run["model_version"] = table.select("model_version").first()[0]
        with _timed(stages, "supabase_write"):
            with _db(spark) as conn:
                execute(conn, "delete from public.customer_scores where org_id = ?::uuid"
                              " and snapshot_month = ?", (org_id, month))
            _jdbc_write(table.coalesce(1), "customer_scores")
    finally:
        table.unpersist()

    gold = {t: spark.read.format("delta").load((gold_dir / t).resolve().as_posix())
            for t in (GOLD_OVERALL, *GOLD_SEGMENTS)}
    agg = segment_aggregate_rows(silver, gold, org_id, month)
    rows["segment_aggregates"] = len(agg)
    with _timed(stages, "supabase_write"), _db(spark) as conn:
        replace_segment_aggregates(conn, org_id, month, agg)


def _error_reason(e):
    return f"Processing error: {type(e).__name__}: {e}"


def process_upload(spark, upload):
    """Run one claimed upload to a final status. Never raises."""
    run_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    run = {"run_id": run_id, "status": "done", "started_at": started.isoformat(), "stage_seconds": {},
           "rows": {}, "dq_summary": None, "recon_summary": None, "model_version": None}
    upload_dir, run_dir = UPLOADS_DIR / upload["id"], RUNS_DIR / run_id
    reason = None
    try:
        validate_upload(upload["org_id"], upload["file_path"], upload["size_bytes"])
        if not (Path(MODEL_DIR) / META_FILE).is_file():
            raise RuntimeError("the saved churn model is missing (models/churn_lr); train it first")
        with _timed(run["stage_seconds"], "download"):
            path = download(upload, upload_dir)
        from src.integration import dataset_worker  # mode 2; imported here to avoid a circular import
        with _db(spark) as conn:
            dataset = dataset_worker.load_dataset(conn, upload["id"])
        if dataset and dataset["kind"] == "generic":
            dataset_worker.train_phase(spark, upload, path, run, run_dir, dataset)
        elif dataset_worker.route(path) == "telco":
            run_pipeline(spark, upload, path, run, run_dir)
        else:
            dataset_worker.profile_phase(spark, upload, path, run, run_dir)
    except Rejected as e:
        run["status"], reason = "rejected", str(e)
    except Exception as e:
        run["status"], reason = "failed", _error_reason(e)
        logger.error("Upload %s failed: %s", upload["id"], format_reason(reason))
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    has_run = bool(run["rows"])
    try:
        with _db(spark) as conn:
            if has_run:
                record_run(conn, run, upload["org_id"], upload)
            set_final_status(conn, upload["id"], run["status"], reason, run_id if has_run else None)
    except Exception as e:  # leave it `processing`; the next start marks it failed after 60 minutes
        logger.error("Could not record final status for upload %s: %s", upload["id"], redact(e))
        run["status"] = "failed"
        reason = _error_reason(e)
    total = round(time.perf_counter() - t0, 2)
    _append_evidence(upload, run, total)
    logger.info("Upload %s -> %s in %.1fs", upload["id"], run["status"], total)
    return {**run, "upload_id": upload["id"], "status_reason": reason and format_reason(reason),
            "total_seconds": total}


def _append_evidence(upload, run, total):
    line = {
        "run_id": run["run_id"], "upload_id": upload["id"], "org_id": upload["org_id"],
        "snapshot_month": upload["snapshot_month"], "status": run["status"],
        "stage_seconds": run["stage_seconds"], "rows": run["rows"], "dq_summary": run["dq_summary"],
        "recon_summary": run["recon_summary"], "total_seconds": total,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    WORKER_EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    with WORKER_EVIDENCE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def process_queue(spark, org_id=None, max_uploads=None):
    """Process queued uploads until none are left (or max_uploads). Returns the result dicts."""
    with _db(spark) as conn:
        fail_stuck(conn, org_id)
    results = []
    while max_uploads is None or len(results) < max_uploads:
        with _db(spark) as conn:
            upload = claim_next(conn, org_id)
        if upload is None:
            break
        logger.info("Claimed upload %s (month %s)", upload["id"], upload["snapshot_month"])
        results.append(process_upload(spark, upload))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Process queued Supabase uploads on this machine.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="process queued uploads, then exit")
    mode.add_argument("--poll", type=int, metavar="SECONDS", help="check for uploads every SECONDS")
    parser.add_argument("--org-id", help="only process uploads of this organisation")
    parser.add_argument("--max", type=int, dest="max_uploads", help="stop after this many uploads")
    args = parser.parse_args(argv)

    spark = get_spark(app_name="supabase-worker")
    try:
        while True:
            done = process_queue(spark, args.org_id, args.max_uploads)
            print(f"Processed {len(done)} upload(s): " + ", ".join(r["status"] for r in done))
            if args.once:
                return
            time.sleep(max(args.poll, 1))
    except KeyboardInterrupt:
        print("Stopped")
    finally:
        spark.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        main()
    except Exception as e:
        print(f"Worker failed: {redact(e)}", file=sys.stderr)
        sys.exit(1)
