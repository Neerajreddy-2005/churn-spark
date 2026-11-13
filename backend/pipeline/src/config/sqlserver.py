import logging
import os

from dotenv import load_dotenv

from src.config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

load_dotenv(PROJECT_ROOT / ".env")

DRIVER = "com.microsoft.sqlserver.jdbc.SQLServerDriver"


def _credentials():
    auth = os.environ.get("SQLSERVER_AUTH", "integrated").lower()
    if auth == "integrated":
        return auth, None, None
    if auth != "sql":
        raise ValueError(f"SQLSERVER_AUTH must be 'integrated' or 'sql', got {auth!r}")
    user, password = os.environ.get("SQLSERVER_USER"), os.environ.get("SQLSERVER_PASSWORD")
    if not user or not password:
        raise ValueError("SQLSERVER_AUTH=sql needs SQLSERVER_USER and SQLSERVER_PASSWORD")
    return auth, user, password


def jdbc_url(database=None):
    auth, _, _ = _credentials()
    host = os.environ.get("SQLSERVER_HOST", "localhost")
    port = os.environ.get("SQLSERVER_PORT", "1433")
    database = database or os.environ.get("SQLSERVER_DB", "churn_db")
    url = f"jdbc:sqlserver://{host}:{port};databaseName={database}"
    if auth == "integrated":
        url += ";integratedSecurity=true"
    url += ";encrypt=true;trustServerCertificate=true"
    logger.debug("SQL Server target: %s (auth=%s)", url, auth)  # never includes the password
    return url


def jdbc_options():
    # The driver option is required: DriverManager does not see the package jar.
    _, user, password = _credentials()
    options = {"driver": DRIVER}
    if user:
        options.update(user=user, password=password)
    return options
