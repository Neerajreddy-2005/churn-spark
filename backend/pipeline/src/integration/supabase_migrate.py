import logging
import sys
from pathlib import Path

from src.config.settings import BACKEND_ROOT
from src.config.supabase import jdbc_props, jdbc_url, redact
from src.ingestion.spark_session import get_spark

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = BACKEND_ROOT / "supabase" / "migrations"

BOOTSTRAP_SQL = """
create schema if not exists private;
create table if not exists private.schema_migrations (
    filename text primary key,
    applied_at timestamptz not null default now()
);
"""


def connect(spark):
    # Go through the JVM driver directly: DriverManager may not see the package jar.
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    for key, value in jdbc_props().items():
        if key != "driver":
            props.setProperty(key, value)
    return jvm.org.postgresql.Driver().connect(jdbc_url(), props)


def _bind(stmt, params):
    for i, value in enumerate(params, start=1):
        stmt.setString(i, None if value is None else str(value))


def execute(conn, sql, params=()):
    """Run one statement or script. Params are bound as strings, so cast in SQL (?::uuid)."""
    if not params:
        stmt = conn.createStatement()
        try:
            stmt.execute(sql)
        finally:
            stmt.close()
        return
    stmt = conn.prepareStatement(sql)
    try:
        _bind(stmt, params)
        stmt.execute()
    finally:
        stmt.close()


def query(conn, sql, params=()):
    """Rows as lists of strings (None for NULL)."""
    stmt = conn.prepareStatement(sql)
    try:
        _bind(stmt, params)
        rs = stmt.executeQuery()
        n = rs.getMetaData().getColumnCount()
        rows = []
        while rs.next():
            rows.append([rs.getString(i) for i in range(1, n + 1)])
        return rows
    finally:
        stmt.close()


class JdbcExecutor:
    def __init__(self, conn):
        self.conn = conn

    def ensure_table(self):
        self.conn.setAutoCommit(True)
        execute(self.conn, BOOTSTRAP_SQL)

    def applied(self):
        return {r[0] for r in query(self.conn, "select filename from private.schema_migrations")}

    def apply(self, filename, sql):
        """The file and its bookkeeping row commit together or not at all."""
        self.conn.setAutoCommit(False)
        try:
            execute(self.conn, sql)
            execute(self.conn, "insert into private.schema_migrations (filename) values (?)", (filename,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.setAutoCommit(True)


def pending(files, applied):
    return [f for f in sorted(files) if f.name not in applied]


def migrate(executor, directory=MIGRATIONS_DIR):
    executor.ensure_table()
    todo = pending(Path(directory).glob("*.sql"), executor.applied())
    for f in todo:
        executor.apply(f.name, f.read_text(encoding="utf-8"))
        logger.info("Applied %s", f.name)
    return [f.name for f in todo]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    spark = get_spark(app_name="supabase-migrate", master="local[1]")
    try:
        conn = connect(spark)
        try:
            applied = migrate(JdbcExecutor(conn))
        finally:
            conn.close()
        print(f"Applied: {', '.join(applied)}" if applied else "Nothing to apply (up to date)")
    except Exception as e:
        print(f"Migration failed: {redact(e)}", file=sys.stderr)
        sys.exit(1)
    finally:
        spark.stop()
