import logging
import os
import re

from dotenv import load_dotenv

from src.config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

load_dotenv(PROJECT_ROOT / ".env")

DRIVER = "org.postgresql.Driver"
_SECRET_VARS = ("SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_PASSWORD")
_SECRET_PATTERNS = [
    re.compile(r"sb_(?:publishable|secret)_[\w-]+"),
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),  # any JWT
]
_runtime_secrets = set()  # tokens and passwords generated while running


def _env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set (see .env)")  # name only, never a value
    return value


def configured():
    names = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
             "SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
    return all(os.environ.get(n) for n in names)


def base_url():
    return _env("SUPABASE_URL").rstrip("/")


def rest_url():
    return base_url() + "/rest/v1"


def anon_key():
    return _env("SUPABASE_ANON_KEY")


def service_key():
    return _env("SUPABASE_SERVICE_ROLE_KEY")


def jdbc_url():
    host, port = _env("SUPABASE_DB_HOST"), os.environ.get("SUPABASE_DB_PORT", "5432")
    # sslmode=require encrypts but does not check who answers. With the project's CA certificate
    # (Supabase dashboard > Database settings > SSL, downloaded to a file) verify the server too.
    ca = os.environ.get("SUPABASE_DB_CA_CERT")
    if ca:
        return f"jdbc:postgresql://{host}:{port}/postgres?sslmode=verify-full&sslrootcert={ca}"
    return f"jdbc:postgresql://{host}:{port}/postgres?sslmode=require"


def jdbc_props():
    return {"user": _env("SUPABASE_DB_USER"), "password": _env("SUPABASE_DB_PASSWORD"), "driver": DRIVER}


def register_secret(value):
    """Mask a runtime-generated secret (access token, test password) in redact()."""
    if value:
        _runtime_secrets.add(value)
    return value


def redact(text):
    """Mask every known secret value and anything shaped like a key or JWT."""
    text = str(text)
    values = {os.environ.get(n) for n in _SECRET_VARS} | _runtime_secrets
    for value in sorted((v for v in values if v), key=len, reverse=True):
        text = text.replace(value, "***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text
