"""Mode 2 live test: real Supabase, real worker. Security of migration 0010 + profile -> review -> train.

Creates two throwaway organisations, uses only synthetic data, and deletes everything afterwards.
Evidence: data/evidence/datasets_live_test.json.
"""
import io
import random
import secrets
import uuid
from datetime import datetime

import openpyxl
import pytest

from src.config.settings import EVIDENCE_DIR
from src.integration import supabase_api as api
from src.integration.supabase_admin import create_org, create_user
from src.integration.supabase_migrate import connect
from src.integration.supabase_worker import process_queue
from src.output.evidence import write_evidence
from tests.test_supabase import Checks, _reachable, cleanup, denied

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def churn_rows(n=600, seed=11):
    """Synthetic subscription data; churn depends on plan and calls; 'Account Status' leaks the outcome."""
    rng = random.Random(seed)
    out = [["CustomerID", "Plan", "Calls", "Spend", "Account Status", "Churn"]]
    for i in range(n):
        plan, calls = rng.choice(["Basic", "Pro", "Max"]), rng.randint(0, 10)
        churn = rng.random() < 0.1 + (0.35 if plan == "Basic" else 0) + 0.04 * calls
        out.append([f"C{i:04d}", plan, "" if i % 25 == 0 else str(calls), f"{rng.uniform(10, 90):.2f}",
                    "Closed" if churn else "Open", "Yes" if churn else "No"])
    return out


def to_csv(rows):
    return ("\n".join(",".join(r) for r in rows) + "\n").encode("utf-8")


def to_xlsx(rows):
    wb = openpyxl.Workbook()
    wb.active.title = "About"
    wb.active.append(["This sheet describes the data"])
    ws = wb.create_sheet("Data")
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(token, org_id, user_id, data, name, suffix, content_type):
    path = f"{org_id}/{uuid.uuid4()}{suffix}"
    status, body = api.storage_upload("uploads", path, data, token, content_type)
    assert status == 200, f"storage upload failed: HTTP {status} {body}"
    status, body = api.rest_insert("uploads", token, {
        "org_id": org_id, "uploaded_by": user_id, "file_path": path, "original_name": name,
        "size_bytes": len(data), "snapshot_month": "2026-12"})
    assert status == 201, f"uploads insert failed: HTTP {status} {body}"
    return path


def _one(table, token, query):
    status, rows = api.rest_select(table, token, query)
    assert status == 200, f"{table}: HTTP {status}"
    return rows


def _rpc_error(status, body, code):
    return status >= 400 and isinstance(body, dict) and body.get("code") == code


@pytest.mark.integration
def test_datasets_live(spark):
    reason = _reachable()
    if reason:
        pytest.skip(reason)
    suffix = secrets.token_hex(4)
    check, seconds = Checks(), {}
    org_ids, user_ids, paths = [], [], []
    try:
        conn = connect(spark)
        try:
            org_a, org_b = create_org(conn, f"ds-test-{suffix}-A"), create_org(conn, f"ds-test-{suffix}-B")
            org_ids += [org_a, org_b]
            user_a, pw_a = create_user(conn, f"ds-test-{suffix}-a@example.com", org_a)
            user_ids.append(user_a)
            user_b, pw_b = create_user(conn, f"ds-test-{suffix}-b@example.com", org_b)
            user_ids.append(user_b)
        finally:
            conn.close()
        token_a = api.sign_in(f"ds-test-{suffix}-a@example.com", pw_a)
        token_b = api.sign_in(f"ds-test-{suffix}-b@example.com", pw_b)

        def run_worker(label):
            t0 = datetime.now()
            results = process_queue(spark, org_id=org_a)
            seconds[label] = round((datetime.now() - t0).total_seconds(), 1)
            return results

        def upload_row(path):
            return _one("uploads", token_a, f"file_path=eq.{path}&select=id,status,status_reason")[0]

        # 1. clear churn CSV -> trained automatically with the suggested columns
        rows = churn_rows()
        paths.append(_upload(token_a, org_a, user_a, to_csv(rows), "subscriptions.csv", ".csv", "text/csv"))
        run_worker("csv_auto_train")
        up = upload_row(paths[-1])
        check("csv: clear churn file trains straight away (status done)", up["status"] == "done")
        d = _one("datasets", token_a, f"upload_id=eq.{up['id']}&select=*")[0]
        sug, prof, res, rev = d["suggestions"], d["profile"], d["result"] or {}, d["review"] or {}
        names = {c["key"]: c["name"] for c in prof["columns"]}
        check("csv: suggested target Churn = Yes, ID CustomerID",
              names.get(sug.get("target")) == "Churn" and sug.get("positive") == "Yes"
              and names.get(sug.get("id")) == "CustomerID")
        check("csv: review marked automatic and equal to the suggestions",
              rev.get("auto") is True and rev.get("target") == sug["target"] and rev.get("features") == sug["features"])
        id_col = next((c for c in prof["columns"] if c["name"] == "CustomerID"), {})
        check("csv: ID values are not stored in the profile", id_col.get("top") == [] and id_col.get("values_hidden"))
        check("csv: all 7 data checks pass", len(d.get("checks") or []) == 7
              and all(c["status"] == "pass" for c in d["checks"]))
        check("csv: leak 'Account Status' removed by the AUC guard",
              [x["name"] for x in (res.get("leakage") or {}).get("removed", [])] == ["Account Status"])
        auc = (res.get("metrics") or {}).get("auc_roc_test") or 0
        check("csv: honest holdout AUC (0.6 to 0.99), not suspicious", 0.6 < auc < 0.99 and res.get("suspicious") is False)
        check("csv: reconciliation all pass", res.get("reconciled") is True)
        scores = _one("dataset_scores", token_a, f"upload_id=eq.{up['id']}&select=row_id&limit=1000")
        check("csv: 600 scores, one per customer", len(scores) == 600 == len({s["row_id"] for s in scores}))
        top = _one("dataset_scores", token_a, f"upload_id=eq.{up['id']}&select=*&order=risk_score.desc&limit=1")
        check("csv: riskiest customer has a reason, never the leak column",
              bool(top and top[0]["reason_1"]) and "Account Status" not in " ".join(
                  filter(None, [top[0]["reason_1"], top[0]["reason_2"], top[0]["reason_3"]])))
        runs = _one("pipeline_runs", token_a, f"upload_id=eq.{up['id']}&select=dq_summary,recon_summary,model_version")
        check("csv: pipeline run recorded (7 checks pass, recon 0 fail)",
              len(runs) == 1 and runs[0]["dq_summary"] == {"pass": 7, "fail": 0}
              and runs[0]["recon_summary"]["fail"] == 0 and runs[0]["model_version"] == "generic-lr")

        # 2. security of the tables and of the review ("Change columns") function
        check("other org: cannot read the dataset", _one("datasets", token_b, f"upload_id=eq.{up['id']}&select=*") == [])
        check("other org: cannot read the scores", _one("dataset_scores", token_b, "select=row_id&limit=5") == [])
        status, body = api.rest_rpc("submit_dataset_review", None, {"p_upload": up["id"], "p_review": {"explore_only": True}})
        check("anon: cannot call submit_dataset_review", status in (401, 403))
        good = {"target": sug["target"], "positive": sug["positive"], "id": sug["id"],
                "features": [k for k in sug["features"] if names[k] != "Spend"]}
        status, body = api.rest_rpc("submit_dataset_review", token_b, {"p_upload": up["id"], "p_review": good})
        check("other org: review rejected as not found", _rpc_error(status, body, "P0002"))
        for label, bad in [("unknown column", {**good, "features": ["c99"]}),
                           ("churn value not in the column", {**good, "positive": "Maybe"}),
                           ("missing features", {k: v for k, v in good.items() if k != "features"}),
                           ("target not a string", {**good, "target": 5})]:
            status, body = api.rest_rpc("submit_dataset_review", token_a, {"p_upload": up["id"], "p_review": bad})
            check(f"review rejected: {label}", _rpc_error(status, body, "22023"))
        check("still done after rejected reviews", upload_row(paths[-1])["status"] == "done")
        status, body = api.rest_update("datasets", token_a, f"upload_id=eq.{up['id']}", {"review": good})
        check("member: cannot update datasets directly", denied(status, body))
        status, body = api.rest_insert("dataset_scores", token_a, {
            "upload_id": up["id"], "org_id": org_a, "row_id": "x", "risk_score": 0.5, "risk_band": "high"})
        check("member: cannot insert dataset_scores", denied(status, body))
        status, body = api.rest_update("uploads", token_a, f"id=eq.{up['id']}", {"status": "queued"})
        check("member: cannot change upload status directly", denied(status, body))

        # 3. retention actions on a dataset customer
        month = "2026-12"
        action = {"org_id": org_a, "customer_id": top[0]["row_id"], "snapshot_month": month,
                  "dataset_id": up["id"], "action_type": "call", "created_by": user_a}
        status, body = api.rest_insert("retention_actions", token_a, action)
        check("action: logged on a scored dataset customer", status == 201)
        for label, bad in [("unknown customer", {**action, "customer_id": "nobody"}),
                           ("wrong month", {**action, "snapshot_month": "2026-01"}),
                           ("someone else as creator", {**action, "created_by": user_b})]:
            status, body = api.rest_insert("retention_actions", token_a, bad)
            check(f"action rejected: {label}", denied(status, body))
        status, body = api.rest_insert("retention_actions", token_b, {**action, "org_id": org_b, "created_by": user_b})
        check("action rejected: other org on this dataset", denied(status, body))
        acts = _one("retention_actions", token_a, f"dataset_id=eq.{up['id']}&select=customer_id,dataset_id")
        check("action: visible to its org with dataset_id", len(acts) == 1 and acts[0]["dataset_id"] == up["id"])
        check("action: not visible to the other org", _one("retention_actions", token_b, "select=id&limit=5") == [])

        # 4. "Change columns": retrain without Spend
        status, body = api.rest_rpc("submit_dataset_review", token_a, {"p_upload": up["id"], "p_review": good})
        check("change columns: accepted after training, upload queued",
              status in (200, 204) and upload_row(paths[-1])["status"] == "queued")
        status, body = api.rest_rpc("submit_dataset_review", token_a, {"p_upload": up["id"], "p_review": good})
        check("change columns: refused while queued", _rpc_error(status, body, "22023"))
        run_worker("csv_retrain")
        d = _one("datasets", token_a, f"upload_id=eq.{up['id']}&select=review,result")[0]
        check("change columns: retrained, review manual, Spend no longer used",
              upload_row(paths[-1])["status"] == "done" and d["review"].get("auto") is False
              and "Spend" not in (d["result"]["features"]["numeric"] + d["result"]["features"]["text"]))
        scores = _one("dataset_scores", token_a, f"upload_id=eq.{up['id']}&select=row_id&limit=1000")
        check("change columns: still exactly 600 scores", len(scores) == 600)
        check("change columns: earlier action kept",
              len(_one("retention_actions", token_a, f"dataset_id=eq.{up['id']}&select=id")) == 1)

        # 5. Excel workbook (binary download path) also trains automatically
        paths.append(_upload(token_a, org_a, user_a, to_xlsx(rows[:301]), "book.xlsx", ".xlsx", XLSX))
        run_worker("xlsx_auto_train")
        up = upload_row(paths[-1])
        d = _one("datasets", token_a, f"upload_id=eq.{up['id']}&select=source_info,profile,review")[0]
        check("xlsx: 'Data' sheet (300 rows) trained automatically", up["status"] == "done"
              and (d.get("source_info") or {}).get("sheet") == "Data" and d["profile"]["rows"] == 300
              and (d.get("review") or {}).get("auto") is True)

        # 6. not churn data: stops and asks; explore-only
        sales = [["Order ID", "Region", "Sales Channel", "Units"]] + [
            [str(1000 + i), random.Random(i).choice(["Asia", "Europe"]), "Online" if i % 2 else "Offline", str(i % 40)]
            for i in range(120)]
        paths.append(_upload(token_a, org_a, user_a, to_csv(sales), "sales.csv", ".csv", "text/csv"))
        run_worker("sales_profile")
        up = upload_row(paths[-1])
        d = _one("datasets", token_a, f"upload_id=eq.{up['id']}&select=suggestions")[0]
        check("sales: no churn column -> waits for review (not trained)",
              up["status"] == "needs_review" and d["suggestions"]["is_churn_data"] is False)
        status, _ = api.rest_rpc("submit_dataset_review", token_a, {"p_upload": up["id"], "p_review": {"explore_only": True}})
        run_worker("sales_explore_only")
        d = _one("datasets", token_a, f"upload_id=eq.{up['id']}&select=result")[0]
        check("sales: explore-only finishes as done without a model",
              status in (200, 204) and upload_row(paths[-1])["status"] == "done" and d.get("result") == {"explore_only": True})

        # 7. unreadable file is rejected with a reason
        paths.append(_upload(token_a, org_a, user_a, b"not really excel", "broken.xlsx", ".xlsx", XLSX))
        run_worker("broken_xlsx")
        up = upload_row(paths[-1])
        check("broken xlsx: rejected with a readable reason",
              up["status"] == "rejected" and "not a readable .xlsx" in (up["status_reason"] or ""))
    finally:
        problems = cleanup(spark, org_ids, user_ids, paths)
        failed = [r["name"] for r in check.results if not r["passed"]]
        write_evidence("datasets_live_test.json", {
            "timestamp": datetime.now().astimezone().isoformat(),
            "worker_run_seconds": seconds,
            "assertions": len(check.results),
            "passed": len(check.results) - len(failed),
            "failed": len(failed),
            "results": check.results,
            "cleanup_problems": len(problems),
        }, EVIDENCE_DIR)
    assert not problems, f"cleanup problems: {problems}"
    assert not failed, f"failed assertions: {failed}"
    assert len(check.results) >= 35
