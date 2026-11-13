"""Mode 2 steps of the worker: route a file, profile it, and (after review) train and score it.

Called from supabase_worker.process_upload, which owns claiming, final status and evidence.
Phase 1 (profile) ends in status 'needs_review'; phase 2 (train) runs once a member has confirmed the
columns through public.submit_dataset_review() and the upload is 'queued' again.
"""
import json
from datetime import datetime, timezone

from pyspark.sql import functions as F

from src.config.settings import REQUIRED_RAW_COLUMNS
from src.generic.checks import can_train, run_checks
from src.generic.profile import profile_table
from src.generic.reader import UnreadableFile, read_table, write_canonical
from src.generic.suggest import redact, suggest
from src.generic.train import TrainingError, train_dataset
from src.integration.supabase_migrate import execute, query
from src.integration.supabase_worker import Rejected, _db, _jdbc_write, _timed, write_check_results
from src.quality import report as dq_report_schema
from src.reconciliation import report as recon_report_schema

NEAR_TELCO = 15        # a CSV with at least this many of the 20 Telco columns is meant as Telco (Mode 1)
MODEL_VERSION = "generic-lr"


def _json(value):
    return json.dumps(value, default=str, allow_nan=False)


def load_dataset(conn, upload_id):
    rows = query(conn, "select kind, profile::text, suggestions::text, review::text from public.datasets"
                       " where upload_id = ?::uuid", (upload_id,))
    if not rows:
        return None
    kind, profile, suggestions, review = rows[0]
    return {"kind": kind, "profile": profile and json.loads(profile),
            "suggestions": suggestions and json.loads(suggestions), "review": review and json.loads(review)}


def _read(path):
    try:
        return read_table(path)
    except UnreadableFile as e:
        raise Rejected(str(e)) from None


def route(path):
    """'telco' for the certified layout (or a near miss, so Mode 1 explains what is wrong), else 'generic'."""
    if path.suffix.lower() != ".csv":
        return "generic"
    header, _, _ = _read(path)
    present = sum(c in header for c in REQUIRED_RAW_COLUMNS)
    return "telco" if present >= NEAR_TELCO else "generic"


def auto_review(profile, suggestions):
    """The suggested columns, when the file is clear enough to train without asking; else None.
    Clear = a churn column found by its name (two values) and all general checks pass with the suggestions."""
    if not suggestions["is_churn_data"]:
        return None
    review = {"target": suggestions["target"], "positive": suggestions["positive"], "id": suggestions["id"],
              "features": suggestions["features"], "auto": True}
    return review if can_train(run_checks(profile, review)) else None


def profile_phase(spark, upload, path, run, run_dir):
    """Read, profile and suggest; store everything. A clear churn file is trained straight away with the
    suggested columns (they stay changeable); anything else waits for a person to confirm the columns."""
    stages = run["stage_seconds"]
    with _timed(stages, "read"):
        header, rows, info = _read(path)
        columns = write_canonical(header, rows, run_dir / "canonical.csv")
    with _timed(stages, "profile"):
        profile = profile_table(spark, run_dir / "canonical.csv", columns)
        suggestions = suggest(profile)
        redact(profile, suggestions["hide_values"])  # ID / personal / identifier values never leave the laptop
    with _timed(stages, "supabase_write"), _db(spark) as conn:
        execute(conn,
                "insert into public.datasets (upload_id, org_id, kind, name, source_info, profile, suggestions)"
                " values (?::uuid, ?::uuid, 'generic', ?, ?::jsonb, ?::jsonb, ?::jsonb)"
                " on conflict (upload_id) do update set source_info = excluded.source_info,"
                " profile = excluded.profile, suggestions = excluded.suggestions, review = null,"
                " checks = null, result = null, updated_at = now()",
                (upload["id"], upload["org_id"], upload.get("original_name") or "dataset",
                 _json(info), _json(profile), _json(suggestions)))
    review = auto_review(profile, suggestions)
    if review is None:
        run["status"] = "needs_review"
        return
    with _db(spark) as conn:
        execute(conn, "update public.datasets set review = ?::jsonb, reviewed_at = now(), updated_at = now()"
                      " where upload_id = ?::uuid", (_json(review), upload["id"]))
    train_phase(spark, upload, path, run, run_dir, {"kind": "generic", "profile": profile, "review": review})


def _check_rows(results, run_id, rows):
    ts = datetime.now(timezone.utc).isoformat()
    return [{"run_id": run_id, "rule_id": r["rule_id"], "description": r["description"], "columns": "",
             "checked_rows": rows, "failing_rows": int(r["failing_rows"]),
             "failing_rate": round(r["failing_rows"] / rows, 6) if rows else 0.0, "threshold": 0.0,
             "status": r["status"], "action": "fail", "run_ts": ts} for r in results]


def _recon_rows(results, run_id):
    ts = datetime.now(timezone.utc).isoformat()
    return [{"run_id": run_id, "check_id": r["check_id"], "description": r["description"],
             "expected": r["expected"], "actual": r["actual"], "difference": None,
             "status": r["status"], "run_ts": ts} for r in results]


def _save(spark, upload_id, checks=None, result=None):
    with _db(spark) as conn:
        execute(conn, "update public.datasets set checks = coalesce(?::jsonb, checks),"
                      " result = coalesce(?::jsonb, result), updated_at = now() where upload_id = ?::uuid",
                (checks and _json(checks), result and _json(result), upload_id))


def train_phase(spark, upload, path, run, run_dir, dataset):
    """Train and score with the reviewed columns. Raises Rejected with a readable reason."""
    review, profile = dataset["review"], dataset["profile"]
    if not review:  # profiled but not reviewed yet: should not have been queued
        raise Rejected("This dataset is waiting for its columns to be confirmed.")
    org_id, run_id, stages = upload["org_id"], run["run_id"], run["stage_seconds"]
    if review.get("explore_only"):
        _save(spark, upload["id"], result={"explore_only": True})
        return

    # Re-check on the worker: the review comes from a browser (the database function checked its shape).
    keys = {c["key"] for c in profile["columns"]}
    used = [review["target"], *review["features"], *([review["id"]] if review.get("id") else [])]
    if not set(used) <= keys:
        raise Rejected("The review names a column that is not in this file.")
    checks = run_checks(profile, review)
    run["rows"]["checks"] = len(checks)
    run["dq_summary"] = {s: sum(r["status"] == s for r in checks) for s in ("pass", "fail")}
    with _timed(stages, "supabase_write"):
        _save(spark, upload["id"], checks=checks)
        write_check_results(spark, _check_rows(checks, run_id, profile["rows"]),
                            dq_report_schema.RESULT_SCHEMA, "dq_results", org_id)
    if not can_train(checks):
        failed = [r for r in checks if r["status"] == "fail"]
        raise Rejected("Data checks failed: " + "; ".join(f"{r['rule_id']} {r['description']} ({r['detail']})"
                                                          for r in failed))

    with _timed(stages, "read"):
        header, rows, _ = _read(path)
        if header != [c["name"] for c in profile["columns"]]:
            raise Rejected("The file no longer matches its profile; upload it again.")
        write_canonical(header, rows, run_dir / "canonical.csv")
    with _timed(stages, "train_and_score"):
        try:
            result, scores = train_dataset(spark, run_dir / "canonical.csv", profile, review)
        except TrainingError as e:
            raise Rejected(str(e)) from None
    try:
        run["rows"]["dataset_scores"] = result["rows"]["total"]
        run["recon_summary"] = {s: sum(r["status"] == s for r in result["reconciliation"]) for s in ("pass", "fail")}
        run["model_version"] = MODEL_VERSION
        with _timed(stages, "supabase_write"):
            write_check_results(spark, _recon_rows(result["reconciliation"], run_id),
                                recon_report_schema.RESULT_SCHEMA, "reconciliation_results", org_id)
            if not result["reconciled"]:
                failed = [r["check_id"] for r in result["reconciliation"] if r["status"] == "fail"]
                _save(spark, upload["id"], result=result)
                raise Rejected(f"Reconciliation failed: {', '.join(failed)}")
            with _db(spark) as conn:
                execute(conn, "delete from public.dataset_scores where upload_id = ?::uuid", (upload["id"],))
            _jdbc_write(scores.select(F.lit(upload["id"]).alias("upload_id"), F.lit(org_id).alias("org_id"),
                                      "row_id", "risk_score", "risk_band", "reason_1", "reason_2", "reason_3",
                                      "churned", "in_training_set").coalesce(1), "dataset_scores")
            _save(spark, upload["id"], result=result)
    finally:
        scores.unpersist()
