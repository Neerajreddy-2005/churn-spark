import argparse
import logging
import secrets
import sys

from src.config.supabase import redact, register_secret
from src.ingestion.spark_session import get_spark
from src.integration.supabase_api import admin_create_user, admin_delete_user
from src.integration.supabase_migrate import connect, execute, query

logger = logging.getLogger(__name__)

ROLES = ("analyst", "manager")


def create_org(conn, name):
    return query(conn, "insert into public.organisations (name) values (?) returning id::text", (name,))[0][0]


def create_user(conn, email, org_id, role="analyst"):
    """Auth user plus profile row. Returns (user_id, password); the caller shows the password once."""
    if not query(conn, "select 1 from public.organisations where id = ?::uuid", (org_id,)):
        raise ValueError("Unknown org id")
    password = register_secret(secrets.token_urlsafe(18))
    user_id = admin_create_user(email, password)
    try:
        execute(conn, "insert into public.profiles (user_id, org_id, role, email)"
                " values (?::uuid, ?::uuid, ?, ?)", (user_id, org_id, role, email))
    except Exception:
        admin_delete_user(user_id)  # no half-created users
        raise
    return user_id, password


def _user_id_by_email(conn, email):
    rows = query(conn, "select id::text from auth.users where lower(email) = lower(?)", (email,))
    if not rows:
        raise ValueError("No user with that email")
    return rows[0][0]


def make_platform_admin(conn, email):
    user_id = _user_id_by_email(conn, email)
    if query(conn, "select 1 from public.profiles where user_id = ?::uuid", (user_id,)):
        raise ValueError("This account belongs to an organisation; platform admins must have no organisation"
                         " - use create-platform-admin with a separate email")
    execute(conn, "insert into private.platform_admins (user_id) values (?::uuid) on conflict do nothing",
            (user_id,))


def create_platform_admin(conn, email):
    """Auth user with NO profile, listed in private.platform_admins. Returns (user_id, password)."""
    if query(conn, "select 1 from auth.users where lower(email) = lower(?)", (email,)):
        raise ValueError("That email is already registered")
    password = register_secret(secrets.token_urlsafe(18))
    user_id = admin_create_user(email, password)
    try:
        execute(conn, "insert into private.platform_admins (user_id) values (?::uuid)", (user_id,))
    except Exception:
        admin_delete_user(user_id)  # no half-created users
        raise
    return user_id, password


def revoke_platform_admin(conn, email):
    execute(conn, "delete from private.platform_admins where user_id = ?::uuid", (_user_id_by_email(conn, email),))


def delete_user(user_id):
    admin_delete_user(user_id)  # profile row goes with it (on delete cascade)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Supabase admin tasks (run by the owner).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-org")
    p.add_argument("--name", required=True)
    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--org-id", required=True)
    p.add_argument("--role", choices=ROLES, default="analyst")
    for name in ("make-platform-admin", "revoke-platform-admin", "create-platform-admin"):
        p = sub.add_parser(name)
        p.add_argument("--email", required=True)
    p = sub.add_parser("delete-user")
    p.add_argument("--user-id", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "delete-user":
        delete_user(args.user_id)
        print("User deleted")
        return
    spark = get_spark(app_name="supabase-admin", master="local[1]")
    try:
        conn = connect(spark)
        try:
            if args.cmd == "create-org":
                print(f"Organisation created: {create_org(conn, args.name)}")
            elif args.cmd == "make-platform-admin":
                make_platform_admin(conn, args.email)
                print("Platform admin granted")
            elif args.cmd == "create-platform-admin":
                user_id, password = create_platform_admin(conn, args.email)
                print(f"Platform admin created: {user_id}")
                print(f"Password (shown once, save it now): {password}")
            elif args.cmd == "revoke-platform-admin":
                revoke_platform_admin(conn, args.email)
                print("Platform admin revoked")
            else:
                user_id, password = create_user(conn, args.email, args.org_id, args.role)
                print(f"User created: {user_id}")
                print(f"Password (shown once, give it to the user now): {password}")
        finally:
            conn.close()
    finally:
        spark.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        main()
    except Exception as e:
        print(f"Failed: {redact(e)}", file=sys.stderr)
        sys.exit(1)
