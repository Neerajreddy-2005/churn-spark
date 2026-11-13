import re
import secrets
import socket
from datetime import datetime
from urllib.parse import urlparse

import pytest

from src.config import supabase as cfg
from src.config.settings import EVIDENCE_DIR
from src.integration import supabase_api as api
from src.integration.supabase_admin import create_org, create_user
from src.integration.supabase_migrate import MIGRATIONS_DIR, connect, execute, migrate, pending, query
from src.output.evidence import write_evidence

CSV = b"a,b\n1,2\n"
SNAPSHOT_RE =re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")  # mirror of the uploads check constraint


# ---- unit (no network) ----

class FakeExecutor:
    def __init__(self, applied=()):
        self.done = set(applied)
        self.log = []

    def ensure_table(self):
        pass

    def applied(self):
        return set(self.done)

    def apply(self, filename, sql):
        self.log.append(filename)
        self.done.add(filename)


def test_migrations_applied_in_filename_order_and_idempotent(tmp_path):
    for name in ("0002_b.sql", "0003_c.sql", "0001_a.sql"):
        (tmp_path / name).write_text("select 1;")
    ex = FakeExecutor()
    assert migrate(ex, tmp_path) == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]
    assert migrate(ex, tmp_path) == []
    assert ex.log == ["0001_a.sql", "0002_b.sql", "0003_c.sql"]


def test_migrate_skips_already_applied(tmp_path):
    for name in ("0001_a.sql", "0002_b.sql"):
        (tmp_path / name).write_text("select 1;")
    ex = FakeExecutor(applied={"0001_a.sql"})
    assert migrate(ex, tmp_path) == ["0002_b.sql"]
    assert [f.name for f in pending(tmp_path.glob("*.sql"), {"0001_a.sql", "0002_b.sql"})] == []


def test_real_migration_files_are_numbered():
    names = [f.name for f in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert names == ["0001_schema.sql", "0002_security.sql", "0003_storage.sql", "0004_hardening.sql",
                     "0005_admin.sql", "0006_force_password_change.sql",
                     "0007_admin_function_grants.sql", "0008_admin_separation.sql",
                     "0009_profile_backfill_and_audit_labels.sql", "0010_datasets.sql", "0011_dataset_actions.sql"]


def test_redact_masks_secrets(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_abc123XYZ")
    monkeypatch.setenv("SUPABASE_DB_PASSWORD", "hunter2-password")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "sb_publishable_pub456")
    cfg.register_secret("runtime-token-value")
    text = ("key=sb_secret_abc123XYZ pw=hunter2-password anon=sb_publishable_pub456 "
            "tok=runtime-token-value jwt=eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl other=sb_secret_unknownkey")
    out = cfg.redact(text)
    for leaked in ("abc123XYZ", "hunter2", "pub456", "runtime-token", "eyJhbGciOi", "unknownkey"):
        assert leaked not in out
    assert out.count("***") == 6


def test_snapshot_month_regex():
    assert SNAPSHOT_RE.match("2026-10")
    assert not SNAPSHOT_RE.match("2026-13")
    assert not SNAPSHOT_RE.match("26-10")
    assert not SNAPSHOT_RE.match("2026-00")


def test_snapshot_regex_matches_migration():
    sql = (MIGRATIONS_DIR / "0001_schema.sql").read_text(encoding="utf-8")
    assert r"'^\d{4}-(0[1-9]|1[0-2])$'" in sql


# ---- integration: RLS proof against the live project ----

OUTPUT_TABLES = ["pipeline_runs", "dq_results", "reconciliation_results", "customer_scores",
                 "segment_aggregates"]
ALL_TABLES = ["organisations", "profiles", "uploads", *OUTPUT_TABLES, "retention_actions"]
OWNER_COLUMN = {"organisations": "id", "profiles": "org_id"}  # everything else uses org_id
DELETE_FILTER = {"profiles": "user_id=not.is.null"}


def _reachable():
    if not cfg.configured():
        return "Supabase env vars not set"
    try:
        socket.create_connection((urlparse(cfg.base_url()).hostname, 443), timeout=5).close()
    except OSError:
        return "Supabase unreachable"
    return None


class Checks:
    def __init__(self):
        self.results = []

    def __call__(self, name, passed):
        self.results.append({"name": name, "passed": bool(passed)})


def denied(status, body):
    """Permission denied or RLS violation, not just any error (42501 is Postgres insufficient_privilege)."""
    return status in (401, 403) and isinstance(body, dict) and body.get("code") == "42501"


def seed_org(conn, org_id, user_id, tag):
    run = f"rls-{tag}"
    path = f"{org_id}/seed.csv"
    for sql, params in [
        ("insert into public.uploads (org_id, uploaded_by, file_path, original_name, size_bytes, snapshot_month)"
         " values (?::uuid, ?::uuid, ?, 'seed.csv', '100', '2026-10')", (org_id, user_id, path)),
        ("insert into public.pipeline_runs (run_id, org_id, snapshot_month, status)"
         " values (?, ?::uuid, '2026-10', 'done')", (run, org_id)),
        ("insert into public.dq_results (run_id, org_id, rule_id) values (?, ?::uuid, 'R1')", (run, org_id)),
        ("insert into public.reconciliation_results (run_id, org_id, check_id) values (?, ?::uuid, 'C1')",
         (run, org_id)),
        ("insert into public.customer_scores (org_id, snapshot_month, customer_id, risk_score, risk_band)"
         " values (?::uuid, '2026-10', 'cust-1', '0.5', 'high')", (org_id,)),
        ("insert into public.segment_aggregates (org_id, snapshot_month, segment_type, segment)"
         " values (?::uuid, '2026-10', 'contract', 'Month-to-month')", (org_id,)),
        ("insert into public.retention_actions (org_id, customer_id, snapshot_month, action_type, created_by)"
         " values (?::uuid, 'cust-1', '2026-10', 'call', ?::uuid)", (org_id, user_id)),
    ]:
        execute(conn, sql, params)


def cleanup(spark, org_ids, user_ids, storage_paths):
    """Delete everything the test made. Order matters: rows that reference users go first."""
    problems = []

    def step(fn, *args):
        try:
            fn(*args)
        except Exception as e:
            problems.append(cfg.redact(e))

    for path in storage_paths:
        step(api.storage_delete, "uploads", path, cfg.service_key())
    conn = connect(spark) if org_ids else None
    try:
        for table in ("retention_actions", "dataset_scores", "datasets", "customer_scores", "segment_aggregates", "dq_results",
                      "reconciliation_results", "pipeline_runs", "uploads"):
            for org_id in org_ids:
                step(execute, conn, f"delete from public.{table} where org_id = ?::uuid", (org_id,))
        for user_id in user_ids:
            step(api.admin_delete_user, user_id)  # profiles go with the user (on delete cascade)
        for org_id in org_ids:
            step(execute, conn, "delete from public.profiles where org_id = ?::uuid", (org_id,))
            step(execute, conn, "delete from public.organisations where id = ?::uuid", (org_id,))
    finally:
        if conn:
            conn.close()
    return problems


@pytest.mark.integration
def test_rls_isolation_live(spark):
    reason = _reachable()
    if reason:
        pytest.skip(reason)

    suffix = secrets.token_hex(4)
    check = Checks()
    org_ids, user_ids, storage_paths = [], [], []
    problems = []
    try:
        conn = connect(spark)
        try:
            org_a, org_b = create_org(conn, f"rls-test-{suffix}-A"), create_org(conn, f"rls-test-{suffix}-B")
            org_ids += [org_a, org_b]
            user_a, pw_a = create_user(conn, f"rls-test-{suffix}-a@example.com", org_a)
            user_ids.append(user_a)
            user_b, _ = create_user(conn, f"rls-test-{suffix}-b@example.com", org_b)
            user_ids.append(user_b)
            seed_org(conn, org_a, user_a, f"{suffix}-a")
            seed_org(conn, org_b, user_b, f"{suffix}-b")
            execute(conn, "insert into public.customer_scores (org_id, snapshot_month, customer_id)"
                          " values (?::uuid, '2026-10', 'only-b')", (org_b,))
            b_action = query(conn, "select id::text from public.retention_actions where org_id = ?::uuid",
                             (org_b,))[0][0]

            token = api.sign_in(f"rls-test-{suffix}-a@example.com", pw_a)

            # A sees only A's rows, on every table.
            for table in ALL_TABLES:
                col = OWNER_COLUMN.get(table, "org_id")
                status, rows = api.rest_select(table, token, "select=*")
                check(f"{table}: select returns only own org rows",
                      status == 200 and isinstance(rows, list) and rows and all(r[col] == org_a for r in rows))
                status, rows = api.rest_select(table, token, f"{col}=eq.{org_b}")
                check(f"{table}: other org's rows not visible by filter", status == 200 and rows == [])

            # A cannot write pipeline outputs.
            for table, row in [
                ("customer_scores", {"org_id": org_a, "snapshot_month": "2026-10", "customer_id": "x"}),
                ("pipeline_runs", {"run_id": f"x-{suffix}", "org_id": org_a}),
                ("dq_results", {"run_id": f"x-{suffix}", "org_id": org_a, "rule_id": "x"}),
                ("reconciliation_results", {"run_id": f"x-{suffix}", "org_id": org_a, "check_id": "x"}),
                ("segment_aggregates", {"org_id": org_a, "snapshot_month": "2026-10",
                                        "segment_type": "x", "segment": "x"}),
            ]:
                check(f"{table}: insert denied", denied(*api.rest_insert(table, token, row)))

            # Uploads: invalid inserts denied.
            def upload_row(**over):
                row = {"org_id": org_a, "uploaded_by": user_a, "file_path": f"{org_a}/{suffix}.csv",
                       "original_name": "t.csv", "size_bytes": 10, "snapshot_month": "2026-10"}
                row.update(over)
                return row

            check("uploads: insert for org B denied",
                  denied(*api.rest_insert("uploads", token, upload_row(org_id=org_b))))
            check("uploads: file_path under org B folder denied",
                  denied(*api.rest_insert("uploads", token, upload_row(file_path=f"{org_b}/{suffix}.csv"))))
            check("uploads: status 'done' denied",
                  denied(*api.rest_insert("uploads", token, upload_row(status="done"))))
            check("uploads: uploaded_by another user denied",
                  denied(*api.rest_insert("uploads", token, upload_row(uploaded_by=user_b))))

            # Retention actions: invalid inserts and cross-org update.
            def action_row(**over):
                row = {"org_id": org_a, "customer_id": "cust-1", "snapshot_month": "2026-10",
                       "action_type": "email", "created_by": user_a}
                row.update(over)
                return row

            check("retention_actions: insert for org B denied",
                  denied(*api.rest_insert("retention_actions", token, action_row(org_id=org_b))))
            check("retention_actions: created_by another user denied",
                  denied(*api.rest_insert("retention_actions", token, action_row(created_by=user_b))))
            status, rows = api.rest_update("retention_actions", token, f"id=eq.{b_action}",
                                           {"outcome": "retained"})
            unchanged = query(conn, "select outcome from public.retention_actions where id = ?::uuid",
                              (b_action,))[0][0] == "pending"
            check("retention_actions: update of org B's row affects 0 rows",
                  status == 200 and rows == [] and unchanged)

            # Profile role cannot be self-assigned; nothing can be deleted.
            check("profiles: update own role denied",
                  denied(*api.rest_update("profiles", token, f"user_id=eq.{user_a}", {"role": "manager"})))
            for table in ALL_TABLES:
                col = OWNER_COLUMN.get(table, "org_id")
                flt = DELETE_FILTER.get(table, f"{col}=eq.{org_a}")
                check(f"{table}: delete denied", denied(*api.rest_delete(table, token, flt)))

            # Allowed writes.
            status, _ = api.rest_insert("uploads", token, upload_row())
            check("uploads: valid insert for own org allowed", status == 201)
            status, _ = api.rest_insert("retention_actions", token, action_row())
            check("retention_actions: valid insert for own org allowed", status == 201)
            own = query(conn, "select count(*) from public.retention_actions where org_id = ?::uuid"
                              " and created_by = ?::uuid", (org_a, user_a))[0][0]
            check("retention_actions: valid insert stored", own == "2")  # seeded row + the new one

            # Storage.
            storage_paths.append(f"{org_a}/test.csv")
            status, _ = api.storage_upload("uploads", f"{org_a}/test.csv", b"a,b\n1,2\n", token, "text/csv")
            check("storage: upload under own org folder allowed", status == 200)
            status, body = api.storage_download("uploads", f"{org_a}/test.csv", cfg.service_key())
            check("storage: uploaded file readable by worker key", status == 200 and "1,2" in str(body))
            storage_paths.append(f"{org_b}/test.csv")  # cleaned up in case it wrongly succeeds
            status, _ = api.storage_upload("uploads", f"{org_b}/test.csv", b"a,b\n1,2\n", token, "text/csv")
            stored_b = query(conn, "select count(*) from storage.objects where bucket_id = 'uploads'"
                                   " and name = ?", (f"{org_b}/test.csv",))[0][0]
            check("storage: upload under org B folder denied", status in (400, 401, 403) and stored_b == "0")
            status, _ = api.storage_upload("uploads", f"{org_a}/anon.csv", b"a,b\n", None, "text/csv")
            check("storage: anonymous upload denied", status in (400, 401, 403))

            # Retention actions must target a customer scored for the same org and month.
            check("retention_actions: customer without a score row denied",
                  denied(*api.rest_insert("retention_actions", token, action_row(customer_id="no-such"))))
            check("retention_actions: customer scored only in org B denied",
                  denied(*api.rest_insert("retention_actions", token, action_row(customer_id="only-b"))))

            # Foreign key: outputs need an existing organisation (as postgres).
            try:
                execute(conn, "insert into public.customer_scores (org_id, snapshot_month, customer_id)"
                              " values (?::uuid, '2026-10', 'x')", ("00000000-0000-4000-8000-000000000000",))
                sqlstate = None
            except Exception as e:  # Py4JJavaError wrapping java.sql.SQLException
                sqlstate = str(e.java_exception.getSQLState()) if hasattr(e, "java_exception") else None
            check("customer_scores: unknown org_id rejected by foreign key (JDBC)",
                  sqlstate == "23503")

            # Upload quota: 20 per org per 24 hours, for table rows and for storage objects.
            have = int(query(conn, "select count(*) from public.uploads where org_id = ?::uuid", (org_a,))[0][0])
            statuses = [api.rest_insert("uploads", token, upload_row(file_path=f"{org_a}/q{i}.csv"))[0]
                        for i in range(20 - have)]
            check("uploads: inserts up to the 20 limit allowed", all(st == 201 for st in statuses))
            check("uploads: 21st insert in 24h denied",
                  denied(*api.rest_insert("uploads", token, upload_row(file_path=f"{org_a}/q-over.csv"))))
            have = int(query(conn, "select count(*) from storage.objects where bucket_id = 'uploads'"
                                   " and name like ?", (f"{org_a}/%",))[0][0])
            statuses = []
            for i in range(20 - have):
                storage_paths.append(f"{org_a}/q{i}.csv")
                statuses.append(api.storage_upload("uploads", f"{org_a}/q{i}.csv", CSV, token, "text/csv")[0])
            check("storage: uploads up to the 20 limit allowed", all(st == 200 for st in statuses))
            storage_paths.append(f"{org_a}/q-over.csv")
            status, _ = api.storage_upload("uploads", f"{org_a}/q-over.csv", CSV, token, "text/csv")
            stored = query(conn, "select count(*) from storage.objects where bucket_id = 'uploads'"
                                 " and name = ?", (f"{org_a}/q-over.csv",))[0][0]
            check("storage: 21st object in 24h denied", status in (400, 401, 403) and stored == "0")

            # Anonymous (publishable key only).
            for table in ALL_TABLES:
                status, rows = api.rest_select(table, None, "select=*")
                check(f"{table}: anonymous sees nothing",
                      status in (401, 403) or (status == 200 and rows == []))
        finally:
            conn.close()
    finally:
        problems = cleanup(spark, org_ids, user_ids, storage_paths)
        failed = [r["name"] for r in check.results if not r["passed"]]
        write_evidence("supabase_rls_test.json", {
            "timestamp": datetime.now().astimezone().isoformat(),
            "assertions": len(check.results),
            "passed": len(check.results) - len(failed),
            "failed": len(failed),
            "results": check.results,
        }, EVIDENCE_DIR)
    assert not problems, f"cleanup problems: {problems}"
    assert not failed, f"failed assertions: {failed}"
    assert len(check.results) > 40
