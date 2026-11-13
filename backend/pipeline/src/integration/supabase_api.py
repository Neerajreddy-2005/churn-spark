import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from src.config.supabase import anon_key, base_url, redact, register_secret, service_key

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30
# How admin calls authenticate; set on first use ("apikey" or "apikey+bearer").
admin_auth_form = None


class SupabaseError(RuntimeError):
    """Message is always redacted."""


def _parse(raw):
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def request(method, path, key, token=None, body=None, headers=None, raw=False):
    """One HTTP call. Returns (status, json_or_text), or (status, bytes) with raw=True.
    HTTP error statuses are returned, not raised."""
    hdrs = {"apikey": key, "User-Agent": "churn-spark"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    data = None
    if isinstance(body, (bytes, bytearray)):
        data = bytes(body)
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(base_url() + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = resp.read()
            return resp.status, (data if raw else _parse(data))
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SupabaseError(redact(f"{method} {path} failed: {e}")) from None


def _require(status, result, what, ok=(200, 201, 204)):
    if status not in ok:
        raise SupabaseError(redact(f"{what} failed: HTTP {status} {result}"))
    return result


def _admin(method, path, body=None):
    """Admin call with the secret key: apikey alone first, then apikey + Bearer if rejected."""
    global admin_auth_form
    key = service_key()
    if admin_auth_form != "apikey+bearer":
        status, result = request(method, path, key, body=body)
        if status not in (401, 403):
            admin_auth_form = "apikey"
            return status, result
    status, result = request(method, path, key, token=key, body=body)
    if status not in (401, 403):
        admin_auth_form = "apikey+bearer"
    return status, result


def admin_create_user(email, password):
    status, result = _admin("POST", "/auth/v1/admin/users",
                            {"email": email, "password": password, "email_confirm": True})
    return _require(status, result, "create user")["id"]


def admin_delete_user(user_id):
    status, result = _admin("DELETE", f"/auth/v1/admin/users/{user_id}")
    _require(status, result, "delete user")


def sign_in(email, password):
    status, result = request("POST", "/auth/v1/token?grant_type=password", anon_key(),
                             body={"email": email, "password": password})
    token = _require(status, result, "sign in", ok=(200,))["access_token"]
    return register_secret(token)


def rest_select(table, token, query=""):
    return request("GET", f"/rest/v1/{table}?{query}", anon_key(), token=token)


def rest_rpc(function, token, args):
    """Call a database function through PostgREST (token=None calls it as anon)."""
    return request("POST", f"/rest/v1/rpc/{function}", anon_key(), token=token, body=args)


def rest_insert(table, token, rows):
    return request("POST", f"/rest/v1/{table}", anon_key(), token=token, body=rows,
                   headers={"Prefer": "return=minimal"})


def rest_update(table, token, query, values):
    # return=representation so callers can count affected rows (RLS hides rows silently).
    return request("PATCH", f"/rest/v1/{table}?{query}", anon_key(), token=token, body=values,
                   headers={"Prefer": "return=representation"})


def rest_delete(table, token, query):
    return request("DELETE", f"/rest/v1/{table}?{query}", anon_key(), token=token,
                   headers={"Prefer": "return=representation"})


def _object_path(bucket, path):
    return f"/storage/v1/object/{bucket}/{urllib.parse.quote(path)}"


def storage_upload(bucket, path, data, token, content_type):
    return request("POST", _object_path(bucket, path), anon_key(), token=token, body=data,
                   headers={"Content-Type": content_type})


def storage_download(bucket, path, key):
    return request("GET", _object_path(bucket, path), key)


def storage_download_bytes(bucket, path, key):
    """Exact file bytes: an .xlsx is binary and a CSV may not be UTF-8, so never decode here."""
    return request("GET", _object_path(bucket, path), key, raw=True)


def storage_delete(bucket, path, key):
    return request("DELETE", _object_path(bucket, path), key)
