import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from datetime import datetime

import pytest

from src.config import supabase as cfg
from src.config.settings import EVIDENCE_DIR
from src.integration import supabase_api as api
from src.integration.supabase_admin import create_org, create_platform_admin, create_user, make_platform_admin
from src.integration.supabase_migrate import MIGRATIONS_DIR, connect, execute, query
from src.output.evidence import write_evidence
from tests.test_supabase import Checks, _reachable, cleanup, denied, seed_org

FUNCTION_PATH = "/functions/v1/admin-users"


def call(token, body, origin=None):
    """POST to the deployed function with the publishable key and the caller's JWT."""
    headers = {"Origin": origin} if origin else None
    return api.request("POST", FUNCTION_PATH, cfg.anon_key(), token=token, body=body, headers=headers)


def test_migration_0005_contents():
    sql = (MIGRATIONS_DIR / "0005_admin.sql").read_text(encoding="utf-8")
    for needle in ("private.platform_admins", "private.is_manager", "private.is_platform_admin", "audit_log"):
        assert needle in sql
    assert "grant insert" not in sql.lower() and "grant all" not in sql.lower()


def test_migration_0006_forces_password_change():
    sql = (MIGRATIONS_DIR / "0006_force_password_change.sql").read_text(encoding="utf-8")
    assert "drop policy profiles_clear_must_change" in sql
    assert "revoke update (must_change_password)" in sql
    assert "on_auth_password_changed" in sql and "not must_change_password" in sql


def test_migration_0009_backfill_constraint_and_labels():
    sql = (MIGRATIONS_DIR / "0009_profile_backfill_and_audit_labels.sql").read_text(encoding="utf-8")
    low = sql.lower()
    assert "update public.profiles p set email = u.email" in low and "p.email is null" in low
    assert "drop constraint if exists audit_log_action_check" in low
    constraint = re.search(r"add constraint audit_log_action_check\s+check \(([^;]*)\);", low)
    assert constraint
    for action in ("create_org", "create_user", "set_role", "deactivate_user", "reactivate_user",
                   "reset_password", "set_name"):
        assert f"'{action}'" in constraint.group(1)
    assert "actor_label" in low and "target_label" in low and "'platform admin ('" in low
    assert "private.platform_admins" in low
    assert "grant" not in low and "create policy" not in low  # no new access for anon/authenticated


def test_edge_function_set_name_and_audit_labels():
    src = (MIGRATIONS_DIR.parent / "functions" / "admin-users" / "index.ts").read_text(encoding="utf-8")
    assert "'set_name'" in src and "actor_label" in src and "target_label" in src
    assert "targetUser(c, body, action === 'set_name')" in src  # self allowed for set_name only
    assert "password" not in re.search(r"async function audit\(.*?\n}\n", src, re.S).group(0).lower()


def totp_code(secret, at=None):
    """RFC 6238 TOTP (HMAC-SHA1, 30 s step, 6 digits) from a base32 secret, stdlib only."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def test_totp_matches_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_code(secret, at=59) == "287082"  # RFC 6238 appendix B, 6-digit truncation of 94287082


def enrol_totp(token):
    """Enrol and verify a TOTP factor through the Auth REST API; returns the aal2 access token.
    The secret is registered for redaction and never printed."""
    status, factor = api.request("POST", "/auth/v1/factors", cfg.anon_key(), token=token,
                                 body={"factor_type": "totp", "friendly_name": f"t-{secrets.token_hex(3)}"})
    assert status in (200, 201), f"enrol failed: HTTP {status}"
    secret = cfg.register_secret(factor["totp"]["secret"])
    fid = factor["id"]
    status, challenge = api.request("POST", f"/auth/v1/factors/{fid}/challenge", cfg.anon_key(), token=token, body={})
    assert status in (200, 201), f"challenge failed: HTTP {status}"
    status, verified = api.request("POST", f"/auth/v1/factors/{fid}/verify", cfg.anon_key(), token=token,
                                   body={"challenge_id": challenge["id"], "code": totp_code(secret)})
    assert status == 200, f"verify failed: HTTP {status}"
    return cfg.register_secret(verified["access_token"])


def test_migration_0008_separation_of_duties():
    sql = (MIGRATIONS_DIR / "0008_admin_separation.sql").read_text(encoding="utf-8")
    low = sql.lower()
    assert re.search(r"create trigger \w+\s+before insert or update of user_id on public\.profiles", low)
    assert re.search(r"create trigger \w+\s+before insert on private\.platform_admins", low)
    assert low.count("security definer set search_path = ''") >= 2
    for fn in ("private.profiles_not_platform_admin()", "private.platform_admin_has_no_profile()"):
        assert f"revoke execute on function {fn} from public, anon, authenticated" in low
    for table in ("profiles", "audit_log", "organisations"):
        policy = re.search(rf"create policy \w+ on public\.{table} for select to authenticated\s+using \(([^;]*)\);", low)
        assert policy, table
        assert "private.is_platform_admin()" in policy.group(1)
        assert "auth.jwt() ->> 'aal') = 'aal2'" in policy.group(1)  # MFA enforced in RLS
    assert "drop policy organisations_select_platform" in low
    for table in ("customer_scores", "segment_aggregates", "uploads", "pipeline_runs", "dq_results",
                  "reconciliation_results", "retention_actions", "objects"):
        assert not re.search(rf"create policy[^;]*\bon (public\.|storage\.)?{table}\b", low), table
    assert "grant" not in low


def test_edge_function_manager_permissions_are_enforced_server_side():
    src = (MIGRATIONS_DIR.parent / "functions" / "admin-users" / "index.ts").read_text(encoding="utf-8")
    assert "'forbidden_role'" in src and "Only the platform admin can create managers" in src
    assert "!c.isPlatformAdmin && userRole !== 'analyst'" in src
    assert "(action === 'set_role' || action === 'reset_password')" in src  # platform admin only
    assert "target.role !== 'analyst'" in src  # managers cannot act on other managers


def test_edge_function_requires_mfa_and_org_for_admin():
    src = (MIGRATIONS_DIR.parent / "functions" / "admin-users" / "index.ts").read_text(encoding="utf-8")
    assert "mfa_required" in src and "aal2" in src
    assert "has_org" in src and "aal: c.aal" in src


def test_make_platform_admin_refuses_account_with_profile(monkeypatch):
    import src.integration.supabase_admin as mod
    calls = []
    monkeypatch.setattr(mod, "_user_id_by_email", lambda conn, email: "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(mod, "query", lambda conn, sql, params=(): [("1",)] if "public.profiles" in sql else [])
    monkeypatch.setattr(mod, "execute", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match="belongs to an organisation"):
        make_platform_admin(object(), "x@example.com")
    assert not calls


def _display_name(conn, user_id):
    return query(conn, "select display_name from public.profiles where user_id = ?::uuid", (user_id,))[0][0]


def _profile(conn, user_id):
    rows = query(conn, "select org_id::text, role, is_active::text, must_change_password::text, email"
                       " from public.profiles where user_id = ?::uuid", (user_id,))
    return rows[0] if rows else None


@pytest.mark.integration
def test_admin_panel_live(spark):
    reason = _reachable()
    if reason:
        pytest.skip(reason)
    status, _ = call(None, {"action": "whoami"})
    if status == 404:
        pytest.skip("admin-users Edge Function is not deployed yet (deploy it, then re-run)")

    suffix = secrets.token_hex(4)
    prefix = f"admin-test-{suffix}"
    check = Checks()
    problems = []
    try:
        conn = connect(spark)
        try:
            org_a, org_b = create_org(conn, f"{prefix}-A"), create_org(conn, f"{prefix}-B")
            mail = lambda tag: f"{prefix}-{tag}@example.com"  # noqa: E731
            mgr_a, pw_mgr_a = create_user(conn, mail("mgr-a"), org_a, "manager")
            ana_a, pw_ana_a = create_user(conn, mail("ana-a"), org_a, "analyst")
            mgr_b, pw_mgr_b = create_user(conn, mail("mgr-b"), org_b, "manager")
            ana_b, _ = create_user(conn, mail("ana-b"), org_b, "analyst")
            pa, pw_pa = create_platform_admin(conn, mail("pa"))  # no organisation, no profile
            seed_org(conn, org_a, mgr_a, prefix)  # business rows a platform admin must never see
            t_mgr_a = api.sign_in(mail("mgr-a"), pw_mgr_a)
            t_ana_a = api.sign_in(mail("ana-a"), pw_ana_a)
            t_mgr_b = api.sign_in(mail("mgr-b"), pw_mgr_b)
            t_pa1 = api.sign_in(mail("pa"), pw_pa)  # password only: aal1
            t_pa = enrol_totp(t_pa1)  # password + TOTP: aal2

            # ---- manager creates an employee in own org (org_id in body ignored) ----
            status, body = call(t_mgr_a, {"action": "create_user", "email": mail("new1"), "display_name": "New One",
                                          "role": "analyst", "org_id": org_b})
            new1 = body.get("user_id") if isinstance(body, dict) else None
            temp = body.get("temporary_password") if isinstance(body, dict) else None
            cfg.register_secret(temp)
            check("create_user: manager gets 200 with user_id and temporary password",
                  status == 200 and bool(new1) and isinstance(temp, str) and len(temp) >= 16)
            prof = _profile(conn, new1) if new1 else None
            check("create_user: org_id in body ignored, user lands in manager's org",
                  bool(prof) and prof[0] == org_a)
            check("create_user: profile has role, email, must_change_password=true",
                  bool(prof) and prof[1] == "analyst" and prof[3] == "true" and prof[4] == mail("new1"))
            try:
                t_new1 = api.sign_in(mail("new1"), temp)
            except Exception:
                t_new1 = None
            check("create_user: temporary password works for sign-in", t_new1 is not None)
            audit = query(conn, "select count(*) from public.audit_log where org_id = ?::uuid and action = 'create_user'"
                                " and target_user_id = ?::uuid and details::text not like '%' || ? || '%'",
                          (org_a, new1, temp or "x"))[0][0]
            check("create_user: audit row written, no password in details", audit == "1")

            # ---- validation ----
            check("create_user: role 'admin' rejected (400)",
                  call(t_mgr_a, {"action": "create_user", "email": mail("x"), "display_name": "X", "role": "admin"})[0] == 400)
            check("create_user: bad email rejected (400)",
                  call(t_mgr_a, {"action": "create_user", "email": "nope", "display_name": "X", "role": "analyst"})[0] == 400)
            check("unknown action rejected (400)", call(t_mgr_a, {"action": "drop_tables"})[0] == 400)

            # ---- who may call ----
            for action_body in ({"action": "create_user", "email": mail("y"), "display_name": "Y", "role": "analyst"},
                                {"action": "set_role", "user_id": mgr_a, "role": "analyst"},
                                {"action": "deactivate_user", "user_id": mgr_a},
                                {"action": "reset_password", "user_id": mgr_a},
                                {"action": "create_org", "org_name": "x", "manager_email": mail("z"), "manager_name": "Z"}):
                check(f"analyst calling {action_body['action']} -> 403", call(t_ana_a, action_body)[0] == 403)
            check("unauthenticated call -> 401", call(None, {"action": "whoami"})[0] == 401)
            status, _ = call(t_mgr_a, {"action": "create_user", "email": mail("evil"), "display_name": "E",
                                       "role": "analyst"}, origin="https://evil.example")
            check("disallowed origin rejected, nothing created",
                  status in (401, 403)
                  and not query(conn, "select 1 from auth.users where email = ?", (mail("evil"),)))
            check("allowed origin accepted", call(t_mgr_a, {"action": "whoami"}, origin="http://localhost:5173")[0] == 200)

            # ---- cross-org and self actions ----
            for action, extra in (("set_role", {"role": "analyst"}), ("deactivate_user", {}), ("reset_password", {})):
                status, _ = call(t_mgr_a, {"action": action, "user_id": mgr_b, **extra})
                check(f"manager cannot {action} a user of another org (403/404)", status in (403, 404))
            other = _profile(conn, mgr_b)
            check("other org's manager unchanged", other[1] == "manager" and other[2] == "true" and other[3] == "false")
            check("manager set_role on self -> 403 (platform admin only)",
                  call(t_mgr_a, {"action": "set_role", "user_id": mgr_a, "role": "analyst"})[0] == 403)
            check("manager cannot deactivate self (400)",
                  call(t_mgr_a, {"action": "deactivate_user", "user_id": mgr_a})[0] == 400)
            check("manager reset_password on self -> 403",
                  call(t_mgr_a, {"action": "reset_password", "user_id": mgr_a})[0] == 403)

            # ---- manager permissions inside own org (tightened rules) ----
            status, body = call(t_pa, {"action": "create_user", "org_id": org_a, "email": mail("mgr2-a"),
                                       "display_name": "Mgr Two", "role": "manager"})
            mgr2 = body.get("user_id") if isinstance(body, dict) else None
            cfg.register_secret(body.get("temporary_password") if isinstance(body, dict) else None)
            check("platform admin creates a second manager in org A", status == 200 and bool(mgr2))
            status, body = call(t_mgr_a, {"action": "create_user", "email": mail("mgr-made"), "display_name": "M",
                                          "role": "manager"})
            check("manager create_user role=manager -> 403 forbidden_role, nothing created",
                  status == 403 and isinstance(body, dict) and body.get("code") == "forbidden_role"
                  and not query(conn, "select 1 from auth.users where email = ?", (mail("mgr-made"),)))
            status, body = call(t_mgr_a, {"action": "create_user", "email": mail("ana-made"), "display_name": "A",
                                          "role": "analyst"})
            cfg.register_secret(body.get("temporary_password") if isinstance(body, dict) else None)
            check("manager create_user role=analyst -> 200", status == 200)
            check("manager set_role on analyst -> 403, role unchanged",
                  call(t_mgr_a, {"action": "set_role", "user_id": ana_a, "role": "manager"})[0] == 403
                  and _profile(conn, ana_a)[1] == "analyst")
            check("manager deactivate another manager in own org -> 403, still active",
                  call(t_mgr_a, {"action": "deactivate_user", "user_id": mgr2})[0] == 403
                  and _profile(conn, mgr2)[2] == "true")
            check("manager reactivate another manager -> 403",
                  call(t_mgr_a, {"action": "reactivate_user", "user_id": mgr2})[0] == 403)
            check("manager deactivate analyst -> 200, then reactivate -> 200",
                  call(t_mgr_a, {"action": "deactivate_user", "user_id": ana_a})[0] == 200
                  and _profile(conn, ana_a)[2] == "false"
                  and call(t_mgr_a, {"action": "reactivate_user", "user_id": ana_a})[0] == 200
                  and _profile(conn, ana_a)[2] == "true")
            check("manager reset_password on analyst -> 403, flag unchanged",
                  call(t_mgr_a, {"action": "reset_password", "user_id": new1})[0] == 403
                  and _profile(conn, new1)[3] == "true")
            check("manager set_name on analyst -> 200",
                  call(t_mgr_a, {"action": "set_name", "user_id": ana_a, "display_name": "Ana Tmp"})[0] == 200)
            check("manager set_name on other manager -> 403, name unchanged",
                  call(t_mgr_a, {"action": "set_name", "user_id": mgr2, "display_name": "Hacked"})[0] == 403
                  and _display_name(conn, mgr2) == "Mgr Two")
            check("manager set_name on self -> 200",
                  call(t_mgr_a, {"action": "set_name", "user_id": mgr_a, "display_name": "Mgr A"})[0] == 200)
            # platform admin keeps every power, including on managers
            check("platform admin set_role (analyst->manager->analyst) works",
                  call(t_pa, {"action": "set_role", "org_id": org_a, "user_id": ana_a, "role": "manager"})[0] == 200
                  and _profile(conn, ana_a)[1] == "manager"
                  and call(t_pa, {"action": "set_role", "org_id": org_a, "user_id": ana_a, "role": "analyst"})[0] == 200
                  and _profile(conn, ana_a)[1] == "analyst")
            check("platform admin can deactivate/reactivate a manager",
                  call(t_pa, {"action": "deactivate_user", "org_id": org_a, "user_id": mgr2})[0] == 200
                  and _profile(conn, mgr2)[2] == "false"
                  and call(t_pa, {"action": "reactivate_user", "org_id": org_a, "user_id": mgr2})[0] == 200)
            check("platform admin can rename a manager",
                  call(t_pa, {"action": "set_name", "org_id": org_a, "user_id": mgr2, "display_name": "Mgr Two"})[0] == 200)
            status, body = call(t_pa, {"action": "reset_password", "org_id": org_a, "user_id": new1})
            temp2 = body.get("temporary_password") if isinstance(body, dict) else None
            cfg.register_secret(temp2)
            check("platform admin reset_password (break-glass): 200, new password works, flag set",
                  status == 200 and bool(temp2) and _profile(conn, new1)[3] == "true"
                  and api.sign_in(mail("new1"), temp2) is not None)

            # ---- set_name ----
            check("set_name: manager renames an analyst in own org",
                  call(t_mgr_a, {"action": "set_name", "user_id": ana_a, "display_name": "  Ana Renamed "})[0] == 200
                  and _display_name(conn, ana_a) == "Ana Renamed")
            check("set_name: manager may rename self",
                  call(t_mgr_a, {"action": "set_name", "user_id": mgr_a, "display_name": "Mgr A"})[0] == 200
                  and _display_name(conn, mgr_a) == "Mgr A")
            before = _display_name(conn, mgr_b)
            check("set_name: cross-org denied (403/404), name unchanged",
                  call(t_mgr_a, {"action": "set_name", "user_id": mgr_b, "display_name": "Hacked"})[0] in (403, 404)
                  and _display_name(conn, mgr_b) == before)
            check("set_name: analyst denied (403), even for self",
                  call(t_ana_a, {"action": "set_name", "user_id": ana_a, "display_name": "Nope"})[0] == 403
                  and _display_name(conn, ana_a) == "Ana Renamed")
            check("set_name: empty name rejected (400)",
                  call(t_mgr_a, {"action": "set_name", "user_id": ana_a, "display_name": "   "})[0] == 400)
            check("set_name: 101-char name rejected (400)",
                  call(t_mgr_a, {"action": "set_name", "user_id": ana_a, "display_name": "x" * 101})[0] == 400)

            # ---- platform admin ----
            who_pa, who_mgr = call(t_pa, {"action": "whoami"})[1], call(t_mgr_a, {"action": "whoami"})[1]
            check("whoami: platform admin true, no org, aal2 / manager false, has org",
                  who_pa.get("is_platform_admin") is True and who_pa.get("has_org") is False
                  and who_pa.get("aal") == "aal2" and who_mgr.get("is_platform_admin") is False
                  and who_mgr.get("has_org") is True and who_mgr.get("role") == "manager")
            check("whoami works at aal1 for the admin (reports aal1)",
                  call(t_pa1, {"action": "whoami"})[1].get("aal") == "aal1")
            check("create_org by manager -> 403",
                  call(t_mgr_a, {"action": "create_org", "org_name": f"{prefix}-M", "manager_email": mail("m1"),
                                 "manager_name": "M"})[0] == 403)
            status, body = call(t_pa, {"action": "create_org", "org_name": f"{prefix}-C", "manager_email": mail("c-mgr"),
                                       "manager_name": "C Mgr"})
            cfg.register_secret(body.get("temporary_password") if isinstance(body, dict) else None)
            new_org = body.get("org_id") if isinstance(body, dict) else None
            prof = _profile(conn, body.get("user_id")) if new_org else None
            check("create_org by platform admin -> 200, first manager created in new org",
                  status == 200 and bool(prof) and prof[0] == new_org and prof[1] == "manager")
            status, body = call(t_pa, {"action": "create_user", "org_id": org_b, "email": mail("pa-made"),
                                       "display_name": "PA Made", "role": "analyst"})
            prof = _profile(conn, body.get("user_id")) if status == 200 else None
            check("platform admin can create a user in a named org", bool(prof) and prof[0] == org_b)
            check("platform admin must name an org (400)",
                  call(t_pa, {"action": "create_user", "email": mail("q"), "display_name": "Q", "role": "analyst"})[0] == 400)
            for action_body in ({"action": "set_role", "user_id": ana_b, "role": "manager"},
                                {"action": "deactivate_user", "user_id": ana_b},
                                {"action": "reset_password", "user_id": ana_b}):
                check(f"platform admin {action_body['action']} without org_id (400)", call(t_pa, action_body)[0] == 400)
                check(f"platform admin {action_body['action']} with the wrong org_id (404)",
                      call(t_pa, {**action_body, "org_id": org_a})[0] == 404)
            check("create_user refuses an email that belongs to a platform admin (409)",
                  call(t_pa, {"action": "create_user", "org_id": org_b, "email": mail("pa"),
                              "display_name": "Dup", "role": "analyst"})[0] == 409)
            status, body = call(t_pa, {"action": "create_user", "org_id": org_a, "email": mail("pa-mgr"),
                                       "display_name": "PA Mgr", "role": "manager"})
            cfg.register_secret(body.get("temporary_password") if isinstance(body, dict) else None)
            check("platform admin can create a manager in a named org",
                  status == 200 and (_profile(conn, body.get("user_id")) or [None, None])[1] == "manager")
            check("platform admin can deactivate a member of another org",
                  call(t_pa, {"action": "deactivate_user", "org_id": org_b, "user_id": ana_b})[0] == 200
                  and _profile(conn, ana_b)[2] == "false")
            call(t_pa, {"action": "reactivate_user", "org_id": org_b, "user_id": ana_b})
            status, body = call(t_pa, {"action": "reset_password", "org_id": org_b, "user_id": ana_b})
            cfg.register_secret(body.get("temporary_password") if isinstance(body, dict) else None)
            check("platform admin can reset a password in another org",
                  status == 200 and _profile(conn, ana_b)[3] == "true")
            check("platform admin set_name needs org_id (400) and works with it",
                  call(t_pa, {"action": "set_name", "user_id": ana_b, "display_name": "Ana B"})[0] == 400
                  and call(t_pa, {"action": "set_name", "org_id": org_b, "user_id": ana_b,
                                  "display_name": "Ana B"})[0] == 200
                  and _display_name(conn, ana_b) == "Ana B")

            # ---- MFA: an aal1 admin is refused by the function and by RLS ----
            status, body = call(t_pa1, {"action": "create_user", "org_id": org_b, "email": mail("aal1"),
                                        "display_name": "A", "role": "analyst"})
            check("admin at aal1: function 403 mfa_required",
                  status == 403 and isinstance(body, dict) and body.get("code") == "mfa_required"
                  and not query(conn, "select 1 from auth.users where email = ?", (mail("aal1"),)))
            status, body = call(t_pa1, {"action": "create_org", "org_name": f"{prefix}-X",
                                        "manager_email": mail("x1"), "manager_name": "X"})
            check("admin at aal1: create_org 403 mfa_required",
                  status == 403 and isinstance(body, dict) and body.get("code") == "mfa_required")
            for table in ("organisations", "profiles", "audit_log"):
                status, rows = api.rest_select(table, t_pa1, "select=*")
                check(f"admin at aal1: {table} returns 0 rows", status == 200 and rows == [])

            # ---- admin-only account (aal2): org-management data yes, business data no ----
            status, rows = api.rest_select("organisations", t_pa, "select=id")
            check("admin aal2: lists all organisations",
                  status == 200 and {org_a, org_b} <= {r["id"] for r in rows})
            for org in (org_a, org_b):
                status, rows = api.rest_select("profiles", t_pa, f"org_id=eq.{org}&select=user_id")
                check("admin aal2: reads members of any org", status == 200 and len(rows) >= 2)
            status, rows = api.rest_select("audit_log", t_pa, "select=org_id")
            check("admin aal2: audit_log across orgs",
                  status == 200 and {org_a, org_b} <= {r["org_id"] for r in rows})
            for table in ("customer_scores", "segment_aggregates", "uploads", "pipeline_runs", "dq_results",
                          "reconciliation_results", "retention_actions"):
                status, rows = api.rest_select(table, t_pa, "select=*")
                check(f"admin aal2: {table} returns 0 rows", status == 200 and rows == [])
            check("seeded business rows exist for the org's own manager (sanity)",
                  len(api.rest_select("customer_scores", t_mgr_a, "select=*")[1]) > 0)

            # ---- database triggers: no dual accounts (via JDBC) ----
            try:
                execute(conn, "insert into public.profiles (user_id, org_id, role, email)"
                              " values (?::uuid, ?::uuid, 'analyst', 'x@example.com')", (pa, org_a))
                dual_profile = True
            except Exception:
                dual_profile = False
            check("trigger: profile for a platform admin is rejected", not dual_profile)
            try:
                execute(conn, "insert into private.platform_admins (user_id) values (?::uuid)", (mgr_a,))
                dual_admin = True
            except Exception:
                dual_admin = False
            check("trigger: platform_admins row for an org member is rejected", not dual_admin)
            try:
                make_platform_admin(conn, mail("mgr-a"))
                cli_refused = False
            except ValueError:
                cli_refused = True
            check("make-platform-admin CLI refuses an account with a profile", cli_refused)
            try:
                create_platform_admin(conn, mail("mgr-a"))
                cli_dup = False
            except ValueError:
                cli_dup = True
            check("create-platform-admin CLI refuses an existing email", cli_dup)
            status, rows = api.rest_select("profiles", t_mgr_a, "select=org_id")
            check("manager cannot read other orgs' profiles", status == 200 and rows and all(r["org_id"] == org_a for r in rows))
            status, rows = api.rest_select("audit_log", t_mgr_a, "select=org_id")
            check("manager cannot read other orgs' audit_log", status == 200 and rows and all(r["org_id"] == org_a for r in rows))

            # ---- deactivation (victim has no pending password change, so org rows are visible) ----
            victim, pw_victim = create_user(conn, mail("victim"), org_a, "analyst")
            t_victim = api.sign_in(mail("victim"), pw_victim)  # session that exists before deactivation
            status, rows = api.rest_select("customer_scores", t_victim, "select=*")
            check("victim sees org rows before deactivation", status == 200 and len(rows) > 0)
            status, _ = call(t_mgr_a, {"action": "deactivate_user", "user_id": victim})
            check("deactivate_user: 200 and profile inactive", status == 200 and _profile(conn, victim)[2] == "false")
            try:
                api.sign_in(mail("victim"), pw_victim)
                signed_in = True
            except Exception:
                signed_in = False
            check("deactivated user cannot sign in", not signed_in)
            status, rows = api.rest_select("customer_scores", t_victim, "select=*")
            check("deactivated user's existing session sees no rows (current_org_id null)",
                  status == 200 and rows == [])
            status, rows = api.rest_select("profiles", t_victim, f"user_id=eq.{victim}")
            check("deactivated user can still read own profile (to show the message)",
                  status == 200 and len(rows) == 1 and rows[0]["is_active"] is False)
            # The ban makes Auth reject the token itself (401) before the function's own
            # is_active check (403) is reached; either way the call is refused.
            check("deactivated user cannot call the function",
                  call(t_victim, {"action": "whoami"})[0] in (401, 403))
            status, _ = call(t_mgr_a, {"action": "reactivate_user", "user_id": victim})
            check("reactivate_user: user can sign in again",
                  status == 200 and _profile(conn, victim)[2] == "true" and api.sign_in(mail("victim"), pw_victim) is not None)

            # ---- forced password change: new1 still holds the temporary password (flag true) ----
            execute(conn, "insert into public.uploads (org_id, uploaded_by, file_path, original_name, size_bytes,"
                          " snapshot_month) values (?::uuid, ?::uuid, ?, 's.csv', '10', '2026-10')",
                    (org_a, mgr_a, f"{org_a}/{suffix}-seed.csv"))
            t1 = api.sign_in(mail("new1"), temp2)
            check("flag is true before the password change", _profile(conn, new1)[3] == "true")
            for col, value in (("role", "manager"), ("is_active", False), ("org_id", org_b), ("email", "x@example.com"),
                               ("must_change_password", False)):
                check(f"profiles: update own {col} denied",
                      denied(*api.rest_update("profiles", t1, f"user_id=eq.{new1}", {col: value})))
            check("profiles: own must_change_password still true after denied update", _profile(conn, new1)[3] == "true")
            status, rows = api.rest_update("profiles", t1, f"user_id=eq.{ana_a}", {"must_change_password": False})
            check("profiles: cannot touch another profile", denied(status, rows) or (status == 200 and rows == []))
            for table in ("customer_scores", "uploads", "organisations", "audit_log"):
                status, rows = api.rest_select(table, t1, "select=*")
                check(f"flag true: {table} returns 0 rows", status == 200 and rows == [])
            status, rows = api.rest_select("profiles", t1, f"user_id=eq.{new1}")
            check("flag true: user can read own profile row",
                  status == 200 and len(rows) == 1 and rows[0]["must_change_password"] is True)
            status, rows = api.rest_select("profiles", t1, "select=user_id")
            check("flag true: user sees only own profile (no org-wide read)",
                  status == 200 and [r["user_id"] for r in rows] == [new1])

            new_pw = secrets.token_urlsafe(18) + "Aa1!"
            cfg.register_secret(new_pw)
            status, _ = api.request("PUT", "/auth/v1/user", cfg.anon_key(), token=t1, body={"password": new_pw})
            check("password change via Auth API succeeds", status == 200)
            check("password change clears must_change_password (trigger)", _profile(conn, new1)[3] == "false")
            t1b = api.sign_in(mail("new1"), new_pw)
            status, rows = api.rest_select("customer_scores", t1b, "select=*")
            check("after password change the user sees org rows", status == 200 and len(rows) > 0)
            status, rows = api.rest_select("uploads", t1b, "select=*")
            check("after password change the user sees org uploads", status == 200 and len(rows) > 0)
            status, rows = api.rest_update("profiles", t1b, f"user_id=eq.{new1}", {"must_change_password": True})
            check("profiles: user cannot write the flag at all",
                  denied(status, rows) and _profile(conn, new1)[3] == "false")

            status, body = call(t_pa, {"action": "reset_password", "org_id": org_a, "user_id": new1})
            temp3 = body.get("temporary_password") if isinstance(body, dict) else None
            cfg.register_secret(temp3)
            check("reset_password after a change: flag true again (final state)",
                  status == 200 and bool(temp3) and _profile(conn, new1)[3] == "true")

            # ---- audit log visibility ----
            status, rows = api.rest_select("audit_log", t_mgr_a, "select=*")
            check("audit_log: manager sees own org's entries only",
                  status == 200 and len(rows) > 0 and all(r["org_id"] == org_a for r in rows))
            status, rows = api.rest_select("audit_log", t_ana_a, "select=*")
            check("audit_log: analyst sees nothing", status == 200 and rows == [])
            status, rows = api.rest_select("audit_log", t_mgr_b, "select=*")
            check("audit_log: other org's manager sees no entries of org A",
                  status == 200 and all(r["org_id"] == org_b for r in rows))
            check("audit_log: insert denied for managers",
                  denied(*api.rest_insert("audit_log", t_mgr_a, {"org_id": org_a, "action": "set_role"})))
            check("audit_log: anonymous sees nothing",
                  api.rest_select("audit_log", None, "select=*")[0] in (401, 403)
                  or api.rest_select("audit_log", None, "select=*")[1] == [])

            # ---- platform admin table is not reachable ----
            status, _ = api.request("GET", "/rest/v1/platform_admins?select=*", cfg.anon_key(), token=t_pa,
                                    headers={"Accept-Profile": "private"})
            check("private.platform_admins not reachable through the Data API", status >= 400)
            status, _ = api.request("POST", "/rest/v1/rpc/admin_is_platform_admin", cfg.anon_key(), token=t_pa,
                                    body={"p_user": pa})
            check("admin_is_platform_admin not callable by users", status in (401, 403, 404))
            status, rows = api.rest_select("organisations", t_mgr_a, "select=id")
            check("manager sees only own organisation", status == 200 and [r["id"] for r in rows] == [org_a])

            # ---- audit labels: readable, and never a password ----
            rows = query(conn, "select action, details->>'actor_label', details->>'target_label', details::text"
                               " from public.audit_log where org_id = ?::uuid and actor_id is not null", (org_a,))
            function_rows = [r for r in rows if r[0] != "create_user" or r[1] is not None]
            check("audit: every row the function wrote has actor_label",
                  len(function_rows) >= 8 and all(r[1] for r in function_rows))
            check("audit: manager actor_label is display name or email",
                  any(r[0] == "set_name" and r[1] == "Mgr A" for r in rows)
                  or any(r[1] == mail("mgr-a") for r in rows))
            check("audit: create_user target_label is the new user's name",
                  any(r[0] == "create_user" and r[2] == "New One" for r in rows))
            check("audit: set_name row carries the new name as target_label",
                  any(r[0] == "set_name" and r[2] == "Ana Renamed" for r in rows))
            check("audit: set_role / reset_password rows have target_label",
                  all(r[2] for r in rows if r[0] in ("set_role", "reset_password", "deactivate_user")))
            pa_rows = query(conn, "select details->>'actor_label' from public.audit_log where org_id = ?::uuid"
                                  " and actor_id = ?::uuid", (org_b, pa))
            check("audit: platform admin actor_label is 'Platform admin (<email>)'",
                  len(pa_rows) >= 1 and all(r[0] == f"Platform admin ({mail('pa')})" for r in pa_rows))
            secrets_seen = [x for x in (temp, temp2, temp3, pw_mgr_a, pw_ana_a, pw_mgr_b, new_pw) if x]
            all_details = " ".join(r[0] or "" for r in query(
                conn, "select details::text from public.audit_log where org_id in (?::uuid, ?::uuid)", (org_a, org_b)))
            check("audit: no password appears in any details", not any(x in all_details for x in secrets_seen))

            # ---- profile email backfill (0009 logic, restricted to the test org) ----
            migration = (MIGRATIONS_DIR / "0009_profile_backfill_and_audit_labels.sql").read_text(encoding="utf-8")
            backfill = migration[migration.index("update public.profiles"):].split(";")[0]
            old_pw = cfg.register_secret(secrets.token_urlsafe(18))
            old_id = api.admin_create_user(mail("old"), old_pw)
            execute(conn, "insert into public.profiles (user_id, org_id, role) values (?::uuid, ?::uuid, 'analyst')",
                    (old_id, org_a))
            check("backfill precondition: old profile has no email", _profile(conn, old_id)[4] is None)
            execute(conn, backfill + " and p.org_id = ?::uuid", (org_a,))
            check("backfill: old profile now carries the auth email", _profile(conn, old_id)[4] == mail("old"))
            check("backfill: existing emails untouched", _profile(conn, mgr_a)[4] == mail("mgr-a"))

            # ---- rate limit: 50 create_user per org per 24h (seed rows, then one more) ----
            execute(conn, "insert into public.audit_log (org_id, actor_id, action)"
                          " select ?::uuid, ?::uuid, 'create_user' from generate_series(1, 50)", (org_a, mgr_a))
            check("create_user: 51st in 24h -> 429",
                  call(t_mgr_a, {"action": "create_user", "email": mail("late"), "display_name": "L",
                                 "role": "analyst"})[0] == 429)
        finally:
            conn.close()
    finally:
        try:
            conn = connect(spark)
            try:
                org_ids = [r[0] for r in query(conn, "select id::text from public.organisations where name like ?",
                                               (f"{prefix}-%",))]
                user_ids = [r[0] for r in query(conn, "select id::text from auth.users where email like ?",
                                                (f"{prefix}-%@example.com",))]
                for org_id in org_ids:
                    execute(conn, "delete from public.audit_log where org_id = ?::uuid", (org_id,))
                if user_ids:  # explicit, though the table cascades with the user
                    for user_id in user_ids:
                        execute(conn, "delete from private.platform_admins where user_id = ?::uuid", (user_id,))
            finally:
                conn.close()
            problems = cleanup(spark, org_ids, user_ids, [])
        except Exception as e:
            problems = [cfg.redact(e)]
        failed = [r["name"] for r in check.results if not r["passed"]]
        write_evidence("admin_panel_test.json", {
            "timestamp": datetime.now().astimezone().isoformat(),
            "assertions": len(check.results),
            "passed": len(check.results) - len(failed),
            "failed": len(failed),
            "results": check.results,
        }, EVIDENCE_DIR)
    assert not problems, f"cleanup problems: {problems}"
    assert not failed, f"failed assertions: {failed}"
    assert len(check.results) > 70
