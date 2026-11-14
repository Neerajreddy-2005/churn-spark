import csv
import secrets
import tempfile
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import supabase as cfg
from src.config.settings import EVIDENCE_DIR, MODEL_DIR, RAW_CSV
from src.evidence.fault_injection import inject_faults
from src.integration import supabase_api as api
from src.integration.supabase_admin import create_org, create_user
from src.integration.supabase_migrate import connect
from src.integration.supabase_worker import (
    Rejected, column_reason, dq_reason, format_reason, process_queue, segment_aggregate_rows, validate_upload,
)
from src.model.train import META_FILE
from src.output.evidence import write_evidence
from src.transformations.gold import build_gold
from tests.test_supabase import Checks, _reachable, cleanup

ORG = "11111111-2222-4333-8444-555555555555"


# ---- unit ----

def test_validate_upload_accepts_good_path():
    validate_upload(ORG, f"{ORG}/abc.csv", 1000)
    validate_upload(ORG, f"{ORG}/abc.csv", 5 * 1024 * 1024)


@pytest.mark.parametrize("path,size", [
    ("99999999-2222-4333-8444-555555555555/abc.csv", 1000),  # another org's folder
    (f"{ORG}/../other/abc.csv", 1000),
    (f"{ORG}/..\\abc.csv", 1000),
    (f"{ORG}/abc.csv", 5 * 1024 * 1024 + 1),
    (f"{ORG}/abc.csv", 0),
    (f"{ORG}/./abc.csv", 1000),           # dot segment
    (f"{ORG}//abc.csv", 1000),            # empty segment
    (f"{ORG}/sub/abc.csv", 1000),         # nested folder (the app never makes one)
    (f"{ORG}/.abc.csv", 1000),            # hidden name
    (f"{ORG}/abc\x00.csv", 1000),         # NUL
    (f"{ORG}/аbc.csv", 1000),             # Cyrillic look-alike letter
])
def test_validate_upload_rejects(path, size):
    with pytest.raises(Rejected):
        validate_upload(ORG, path, size)


def test_set_final_status_only_while_processing(monkeypatch):
    """A row that fail_stuck already marked 'failed' must not be flipped to 'done' by a slow run."""
    from src.integration import supabase_worker as w
    seen = {}

    def fake_query(conn, sql, params):
        seen["sql"] = sql
        return []  # no row matched: the upload is no longer 'processing'
    monkeypatch.setattr(w, "query", fake_query)
    assert w.set_final_status(None, ORG, "done", None, "run1") is False
    assert "status = 'processing'" in seen["sql"] and "returning" in seen["sql"]


def test_error_reason_hides_internals():
    from src.integration.supabase_worker import _error_reason
    from src.reconciliation.checks import ReconciliationError
    run_id = "0123456789abcdef"
    shown = _error_reason(RuntimeError("Py4JJavaError at /opt/app/src/x.py org.apache.spark.SparkException"), run_id)
    assert "Py4J" not in shown and "/opt" not in shown and "spark" not in shown.lower() and "01234567" in shown
    assert _error_reason(ReconciliationError("Reconciliation failed: r01"), run_id) == "Reconciliation failed: r01"


def test_format_reason_truncates_and_redacts(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_abc123XYZ")
    out = format_reason("bad\nkey sb_secret_abc123XYZ " + "x" * 1000)
    assert len(out) == 500 and out.endswith("...")
    assert "abc123XYZ" not in out and "\n" not in out
    assert format_reason("short") == "short"


@pytest.mark.parametrize("outcome", ["done", "failed"])
def test_process_upload_removes_its_run_dir(tmp_path, monkeypatch, outcome):
    """Audit finding #4: every run's working files used to stay in data/runs forever."""
    from contextlib import nullcontext

    from src.integration import dataset_worker
    from src.integration import supabase_worker as w

    model = tmp_path / "model"
    model.mkdir()
    (model / META_FILE).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(w, "MODEL_DIR", model)
    monkeypatch.setattr(w, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(w, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(w, "WORKER_EVIDENCE", tmp_path / "worker_runs.jsonl")
    monkeypatch.setattr(w, "validate_upload", lambda *a: None)
    monkeypatch.setattr(w, "_db", lambda spark: nullcontext(None))
    monkeypatch.setattr(w, "set_final_status", lambda *a: None)
    monkeypatch.setattr(w, "record_run", lambda *a: None)

    def fake_download(upload, dest_dir):
        dest_dir.mkdir(parents=True)
        (dest_dir / "f.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        return dest_dir / "f.csv"

    def fake_profile(spark, upload, path, run, run_dir):
        run_dir.mkdir(parents=True)
        (run_dir / "canonical.csv").write_text("c0,c1,row\n1,2,1\n", encoding="utf-8")
        run["rows"] = {"rows_accepted": 1}
        if outcome == "failed":
            raise RuntimeError("boom")

    monkeypatch.setattr(w, "download", fake_download)
    monkeypatch.setattr(dataset_worker, "load_dataset", lambda conn, upload_id: None)
    monkeypatch.setattr(dataset_worker, "route", lambda path: "generic")
    monkeypatch.setattr(dataset_worker, "profile_phase", fake_profile)

    upload = {"id": str(uuid.uuid4()), "org_id": ORG, "file_path": f"{ORG}/f.csv", "size_bytes": "8",
              "snapshot_month": "2026-01", "original_name": "f.csv", "uploaded_by": None}
    result = w.process_upload(None, upload)
    assert result["status"] == outcome
    assert not (tmp_path / "runs" / result["run_id"]).exists()
    assert not (tmp_path / "uploads" / upload["id"]).exists()


def test_segment_aggregate_rows_money(spark):
    rows = [  # customer, contract, internet, payment, tenure, band, charges, churn
        ("a", "Month-to-month", "DSL", "Mailed check", 3, "0-12", 10.10, True),
        ("b", "Month-to-month", "DSL", "Mailed check", 30, "25-48", 20.20, False),
        ("c", "Two year", "No", "Mailed check", 60, "49-72", 30.30, True),
    ]
    silver = spark.createDataFrame(rows, ["customer_id", "contract", "internet_service", "payment_method",
                                          "tenure_months", "tenure_band", "monthly_charges", "churn"])
    silver = silver.withColumn("churn_flag", silver["churn"].cast("int"))
    out = segment_aggregate_rows(silver, build_gold(silver), ORG, "2026-10")
    by = {(r["segment_type"], r["segment"]): r for r in out}

    overall = by[("overall", "All")]
    assert (overall["customers"], overall["churned"]) == (3, 2)
    assert overall["churn_rate_pct"] == Decimal("66.67")
    assert overall["mrr"] == Decimal("60.60") and overall["mrr_lost"] == Decimal("40.40")
    mtm = by[("contract", "Month-to-month")]
    assert (mtm["customers"], mtm["churned"]) == (2, 1)
    assert mtm["mrr"] == Decimal("30.30") and mtm["mrr_lost"] == Decimal("10.10")
    assert by[("contract", "Two year")]["mrr_lost"] == Decimal("30.30")
    assert by[("internet_service", "DSL")]["mrr_lost"] == Decimal("10.10")
    assert by[("tenure_band", "25-48")]["mrr_lost"] == Decimal("0.00")
    assert {r["org_id"] for r in out} == {ORG} and {r["snapshot_month"] for r in out} == {"2026-10"}
    assert {r["segment_type"] for r in out} == {"overall", "contract", "internet_service",
                                                "payment_method", "tenure_band"}


# ---- integration: live Supabase, real CSV ----

def _select(table, token, query):
    """All rows via limit/offset pages (PostgREST returns at most 1000 per request)."""
    order = "&order=customer_id" if table == "customer_scores" else ""
    rows, offset = [], 0
    while True:
        status, page = api.rest_select(table, token, f"{query}{order}&limit=1000&offset={offset}")
        assert status == 200 and isinstance(page, list), f"{table} select failed: HTTP {status}"
        rows += page
        if len(page) < 1000:
            return rows
        offset += 1000


def _upload(token, org_id, user_id, data, month, name="t.csv"):
    path = f"{org_id}/{uuid.uuid4()}.csv"
    status, _ = api.storage_upload("uploads", path, data, token, "text/csv")
    assert status == 200, f"storage upload failed: HTTP {status}"
    status, _ = api.rest_insert("uploads", token, {
        "org_id": org_id, "uploaded_by": user_id, "file_path": path, "original_name": name,
        "size_bytes": len(data), "snapshot_month": month})
    assert status == 201, f"uploads insert failed: HTTP {status}"
    return path


def _run_worker(spark, org_id):
    start = time.perf_counter()
    results = process_queue(spark, org_id=org_id)
    return results, round(time.perf_counter() - start, 1)


def _money_lost():
    with open(RAW_CSV, newline="", encoding="utf-8") as f:
        total = sum((Decimal(r["MonthlyCharges"]) for r in csv.DictReader(f) if r["Churn"] == "Yes"),
                    Decimal(0))
    return total.quantize(Decimal("0.01"))


@pytest.mark.integration
def test_worker_end_to_end_live(spark):
    reason = _reachable()
    if reason:
        pytest.skip(reason)
    if not RAW_CSV.is_file():
        pytest.skip("RAW_CSV missing")
    if not (Path(MODEL_DIR) / META_FILE).is_file():
        pytest.skip("saved model missing")

    suffix = secrets.token_hex(4)
    check = Checks()
    run_seconds = {}
    org_ids, user_ids, storage_paths = [], [], []
    try:
        conn = connect(spark)
        try:
            org_a, org_b = create_org(conn, f"worker-test-{suffix}-A"), create_org(conn, f"worker-test-{suffix}-B")
            org_ids += [org_a, org_b]
            user_a, pw_a = create_user(conn, f"worker-test-{suffix}-a@example.com", org_a)
            user_ids.append(user_a)
            user_b, pw_b = create_user(conn, f"worker-test-{suffix}-b@example.com", org_b)
            user_ids.append(user_b)
        finally:
            conn.close()
        token_a = api.sign_in(f"worker-test-{suffix}-a@example.com", pw_a)
        token_b = api.sign_in(f"worker-test-{suffix}-b@example.com", pw_b)
        data = RAW_CSV.read_bytes()

        def upload_status(path):
            return _select("uploads", token_a, f"file_path=eq.{path}&select=*")[0]

        # 1. real CSV, 2026-10
        storage_paths.append(_upload(token_a, org_a, user_a, data, "2026-10"))
        results, run_seconds["real_csv_2026-10"] = _run_worker(spark, org_a)
        check("real csv: one upload processed", len(results) == 1)
        res = results[0]
        up = upload_status(storage_paths[-1])
        check("real csv: upload status done", up["status"] == "done" and up["run_id"] == res["run_id"])
        run = _select("pipeline_runs", token_a, f"run_id=eq.{res['run_id']}&select=*")
        check("real csv: pipeline_runs row with dq fail = 0 and recon fail = 0",
              len(run) == 1 and run[0]["status"] == "done" and run[0]["dq_summary"]["fail"] == 0
              and run[0]["recon_summary"]["fail"] == 0 and run[0]["recon_summary"]["pass"] > 0)
        scores = _select("customer_scores", token_a, "snapshot_month=eq.2026-10&select=customer_id")
        check("real csv: 7043 customer_scores rows", len(scores) == 7043 == len({r["customer_id"] for r in scores}))
        overall = _select("segment_aggregates", token_a,
                          "snapshot_month=eq.2026-10&segment_type=eq.overall&select=*")
        o = overall[0] if len(overall) == 1 else {}
        check("real csv: overall churned 1869 / customers 7043 / 26.54%",
              (o.get("churned"), o.get("customers"), Decimal(str(o.get("churn_rate_pct")))) ==
              (1869, 7043, Decimal("26.54")))
        check("real csv: overall mrr_lost equals CSV sum of churned MonthlyCharges",
              Decimal(str(o.get("mrr_lost"))).quantize(Decimal("0.01")) == _money_lost())
        dq = _select("dq_results", token_a, f"run_id=eq.{res['run_id']}&select=rule_id")
        check("real csv: 20 dq_results rows", len(dq) == 20)
        recon = _select("reconciliation_results", token_a, f"run_id=eq.{res['run_id']}&select=status")
        check("real csv: all reconciliation checks pass", recon and all(r["status"] == "pass" for r in recon))
        check("worker removed the downloaded input", not (Path(RAW_CSV).parents[1] / "uploads" / up["id"]).exists())

        # 2. fault-injected CSV, 2026-11
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            faulty = Path(tmp) / "faulty.csv"
            inject_faults(RAW_CSV, faulty, n_per_rule=3)
            storage_paths.append(_upload(token_a, org_a, user_a, faulty.read_bytes(), "2026-11", "faulty.csv"))
        results, run_seconds["faulty_csv_2026-11"] = _run_worker(spark, org_a)
        up = upload_status(storage_paths[-1])
        check("faulty csv: upload rejected", len(results) == 1 and up["status"] == "rejected")
        check("faulty csv: status_reason lists failed rule ids",
              "dq03" in (up["status_reason"] or "") and "dq14" in (up["status_reason"] or ""))
        dq = _select("dq_results", token_a, f"run_id=eq.{up['run_id']}&select=rule_id,status")
        check("faulty csv: dq_results stored for the rejected run",
              len(dq) == 20 and any(r["status"] == "fail" for r in dq))
        check("faulty csv: no customer_scores for 2026-11",
              _select("customer_scores", token_a, "snapshot_month=eq.2026-11&select=customer_id") == [])

        # 3. re-process 2026-10: idempotent
        storage_paths.append(_upload(token_a, org_a, user_a, data, "2026-10"))
        results, run_seconds["reprocess_2026-10"] = _run_worker(spark, org_a)
        up = upload_status(storage_paths[-1])
        scores = _select("customer_scores", token_a, "snapshot_month=eq.2026-10&select=customer_id")
        overall = _select("segment_aggregates", token_a, "snapshot_month=eq.2026-10&segment_type=eq.overall&select=customers")
        check("reprocess: upload done", len(results) == 1 and up["status"] == "done")
        check("reprocess: still exactly 7043 customer_scores rows", len(scores) == 7043)
        check("reprocess: one overall aggregate row", len(overall) == 1)

        # 4. another org sees nothing
        for table in ("uploads", "pipeline_runs", "dq_results", "reconciliation_results",
                      "customer_scores", "segment_aggregates"):
            status, rows = api.rest_select(table, token_b, "select=*&limit=5")
            check(f"other org: {table} not readable", status == 200 and rows == [])
    finally:
        problems = cleanup(spark, org_ids, user_ids, storage_paths)
        failed = [r["name"] for r in check.results if not r["passed"]]
        write_evidence("worker_e2e_test.json", {
            "timestamp": datetime.now().astimezone().isoformat(),
            "worker_run_seconds": run_seconds,
            "assertions": len(check.results),
            "passed": len(check.results) - len(failed),
            "failed": len(failed),
            "results": check.results,
            "cleanup_problems": len(problems),
        }, EVIDENCE_DIR)
    assert not problems, f"cleanup problems: {problems}"
    assert not failed, f"failed assertions: {failed}"
    assert len(check.results) >= 18


def test_column_reason_other_layout():
    from src.config.settings import REQUIRED_RAW_COLUMNS
    bank = ["RowNumber", "CustomerId", "Surname", "CreditScore", "Geography", "Gender", "Age", "Tenure"]
    text = column_reason(list(REQUIRED_RAW_COLUMNS), bank)
    assert text.startswith("This file does not look like the Telco churn layout: 20 of the 20")
    assert "[" not in text and "Column names are case-sensitive." in text  # Gender vs gender


def test_column_reason_small_mismatch():
    text = column_reason(["tenure"], ["Region"])
    assert text.startswith("Missing columns: tenure. Columns that are not part of the layout: Region.")
    assert "Churn is optional" in text and "case-sensitive" not in text


def test_dq_reason_lists_ids_descriptions_and_counts():
    results = [{"rule_id": "dq04", "description": "customerID unique", "status": "fail", "failing_rows": 20},
               {"rule_id": "dq10", "description": "x", "status": "warn", "failing_rows": 3}]
    assert dq_reason(results) == "Data quality failed: dq04. Details: dq04 customerID unique (20 rows)"
    many = [{"rule_id": f"dq{i:02d}", "description": "x" * 60, "status": "fail", "failing_rows": 1} for i in range(17)]
    assert "dq16" in format_reason(dq_reason(many))  # every id survives the 500-char cap


def test_segment_aggregate_rows_unlabelled(spark):
    rows = [("a", "Month-to-month", "DSL", "Mailed check", 3, "0-12", 10.10, None),
            ("b", "Two year", "DSL", "Mailed check", 80, "73+", 20.20, None)]
    silver = spark.createDataFrame(rows, "customer_id string, contract string, internet_service string, "
                                         "payment_method string, tenure_months int, tenure_band string, "
                                         "monthly_charges double, churn boolean")
    silver = silver.withColumn("churn_flag", silver["churn"].cast("int"))
    out = segment_aggregate_rows(silver, build_gold(silver), ORG, "2026-12")
    overall = next(r for r in out if r["segment_type"] == "overall")
    assert overall["customers"] == 2 and overall["mrr"] == Decimal("30.30")
    assert overall["churned"] is None and overall["churn_rate_pct"] is None and overall["mrr_lost"] is None
    assert all(r["churned"] is None and r["mrr_lost"] is None for r in out)


def test_column_reason_semicolons():
    from src.config.settings import REQUIRED_RAW_COLUMNS
    text = column_reason(list(REQUIRED_RAW_COLUMNS), [";".join(REQUIRED_RAW_COLUMNS)])
    assert "separated by semicolons" in text
