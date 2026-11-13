import os
import sys
from pathlib import Path

from pyspark.sql import SparkSession

from src.config.settings import JARS_DIR, MSSQL_JDBC_PACKAGE, POSTGRES_JDBC_PACKAGE, REQUIRED_JARS


def _check_java_home():
    # A wrong JAVA_HOME makes the Spark launcher exit silently and PySpark then
    # waits forever, so fail fast with a readable message instead.
    java_home = os.environ.get("JAVA_HOME")
    if java_home and not any((Path(java_home) / "bin" / exe).is_file() for exe in ("java.exe", "java")):
        raise RuntimeError(
            f"JAVA_HOME={java_home!r} has no bin\\java. Point it at the JDK folder "
            "(not its bin folder), e.g. C:\\Program Files\\Eclipse Adoptium\\jdk-17..., "
            "then restart the terminal/VS Code."
        )


def get_spark(app_name="churn-spark", master="local[*]"):
    active = SparkSession.getActiveSession()
    if active is not None:  # e.g. Databricks notebooks already have a session
        return active

    _check_java_home()
    # Python workers (createDataFrame etc.) must run in this interpreter, not whatever
    # "python" is first on PATH (on Windows that can be the Store shortcut).
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)

    from delta import configure_spark_with_delta_pip

    builder = (
        SparkSession.builder.appName(app_name)
        .master(master)
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.shuffle.partitions", "4")
        # Delta reads its transaction log with 50 tasks by default; far too many for local tables.
        .config("spark.databricks.delta.snapshotPartitions", "2")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.ui.enabled", "false")
    )
    local_jars = sorted(JARS_DIR.glob("*.jar")) if JARS_DIR.is_dir() else []
    if all(any(j.name.startswith(r) for j in local_jars) for r in REQUIRED_JARS):
        # Local jars: no Maven/Ivy network lookup at startup (it sometimes hung for ~10 minutes).
        # file:// URIs: on Windows a bare "C:/..." is read as a URL with scheme "C".
        builder = builder.config("spark.jars", ",".join(j.resolve().as_uri() for j in local_jars))
        spark = builder.getOrCreate()
    else:
        packages = [MSSQL_JDBC_PACKAGE, POSTGRES_JDBC_PACKAGE]
        spark = configure_spark_with_delta_pip(builder, extra_packages=packages).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    try:
        # Silence the harmless Windows "Failed to delete Spark temp dir" error at shutdown.
        jvm = spark._jvm
        for name in ("org.apache.spark.util.ShutdownHookManager", "org.apache.spark.SparkEnv"):
            jvm.org.apache.logging.log4j.core.config.Configurator.setLevel(
                name, jvm.org.apache.logging.log4j.Level.OFF
            )
    except Exception:
        pass
    return spark
