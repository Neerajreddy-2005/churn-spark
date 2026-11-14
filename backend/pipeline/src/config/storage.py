"""Where Delta tables live: path-based Delta on the laptop, Unity Catalog tables on Databricks.

Stages always pass a path under DATA_ROOT. With CHURN_UC_SCHEMA unset (the laptop) that path is
used as-is. With CHURN_UC_SCHEMA="catalog.schema" (the Databricks job) the same path maps to a
managed table, e.g. DATA_ROOT/silver/telco_customers -> catalog.schema.silver_telco_customers.
"""
import os
from pathlib import Path

from src.config.settings import DATA_ROOT

ON_DATABRICKS = "DATABRICKS_RUNTIME_VERSION" in os.environ
UC_SCHEMA = os.environ.get("CHURN_UC_SCHEMA")


def table_name(path):
    """DATA_ROOT/<area>/<name> -> catalog.schema.<area>_<name>, or just <name> when it already
    starts with the area (gold/gold_churn_overall -> gold_churn_overall)."""
    try:
        parts = Path(path).resolve().relative_to(Path(DATA_ROOT).resolve()).parts
    except ValueError:
        raise ValueError(f"{path} is not under DATA_ROOT ({DATA_ROOT}); cannot map it to a table")
    name = parts[-1] if len(parts) == 2 and parts[1].startswith(parts[0]) else "_".join(parts)
    return f"{UC_SCHEMA}.{name}"


def _location(path):
    return Path(path).resolve().as_posix()


def read_delta(spark, path):
    if UC_SCHEMA:
        return spark.table(table_name(path))
    return spark.read.format("delta").load(_location(path))


def write_delta(df, path, mode="overwrite", overwrite_schema=False, partition_by=None, replace_where=None):
    writer = df.write.format("delta").mode(mode)
    if overwrite_schema:
        writer = writer.option("overwriteSchema", "true")
    if partition_by:
        writer = writer.partitionBy(partition_by)
    if replace_where:
        writer = writer.option("replaceWhere", replace_where)
    if UC_SCHEMA:
        writer.saveAsTable(table_name(path))
    else:
        writer.save(_location(path))


def delta_exists(spark, path):
    from delta.tables import DeltaTable
    if UC_SCHEMA:
        return spark.catalog.tableExists(table_name(path))
    return DeltaTable.isDeltaTable(spark, _location(path))


def delta_table(spark, path):
    from delta.tables import DeltaTable
    if UC_SCHEMA:
        return DeltaTable.forName(spark, table_name(path))
    return DeltaTable.forPath(spark, _location(path))


def cache(df):
    # Serverless compute rejects PERSIST; there the plan is simply recomputed.
    return df if ON_DATABRICKS else df.cache()


def uncache(df):
    if not ON_DATABRICKS:
        df.unpersist()
