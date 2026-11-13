import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from src.config.settings import SQL_DDL_DIR
from src.config.sqlserver import jdbc_options, jdbc_url
from src.ingestion.spark_session import get_spark
from src.output.evidence import write_evidence

logger = logging.getLogger(__name__)

DATABASE_FILE = "00_create_database.sql"  # runs against master; the rest against the database


def split_batches(text):
    """Split T-SQL on lines that contain only GO."""
    batches = re.split(r"^[ \t]*GO[ \t]*\r?$", text, flags=re.MULTILINE | re.IGNORECASE)
    return [b.strip() for b in batches if b.strip()]


def _check_name(database):
    if not re.fullmatch(r"\w+", database):
        raise ValueError(f"Bad database name: {database!r}")


def execute_sql(spark, database, statements):
    # Go through the JVM driver directly: DriverManager may not see the package jar.
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    for key, value in jdbc_options().items():
        if key != "driver":
            props.setProperty(key, value)
    conn = jvm.com.microsoft.sqlserver.jdbc.SQLServerDriver().connect(jdbc_url(database), props)
    try:
        stmt = conn.createStatement()
        try:
            for sql in statements:
                stmt.execute(sql)
        finally:
            stmt.close()
    finally:
        conn.close()


def run_ddl(spark, database="churn_db", ddl_dir=SQL_DDL_DIR):
    _check_name(database)
    files = sorted(Path(ddl_dir).glob("*.sql"))
    for f in files:
        text = f.read_text(encoding="utf-8").replace("churn_db", database)
        target = "master" if f.name == DATABASE_FILE else database
        batches = split_batches(text)
        execute_sql(spark, target, batches)
        logger.info("Ran %s against %s (%d statements)", f.name, target, len(batches))
    return [f.name for f in files]


def drop_database(spark, database):
    _check_name(database)
    execute_sql(spark, "master", [
        f"IF DB_ID('{database}') IS NOT NULL BEGIN "
        f"ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
        f"DROP DATABASE [{database}]; END"
    ])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start = time.perf_counter()
    spark = get_spark()
    try:
        database = os.environ.get("SQLSERVER_DB", "churn_db")
        files = run_ddl(spark, database)
        elapsed = time.perf_counter() - start
        print(f"Database {database}: ran {', '.join(files)}")
        print(f"Elapsed seconds: {elapsed:.1f}")
        write_evidence("sqlserver_setup.json", {
            "database": database, "files": files,
            "timestamp": datetime.now().astimezone().isoformat(), "seconds": round(elapsed, 1),
        })
    finally:
        spark.stop()
