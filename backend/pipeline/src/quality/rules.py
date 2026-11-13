from pyspark.sql import functions as F

from src.config.settings import EXPECTED_RAW_COLUMNS


def _cols(rule):
    if rule["columns"] == ["*"]:
        return EXPECTED_RAW_COLUMNS
    return rule["columns"]


def _count(df, cond):
    return df.filter(F.coalesce(cond, F.lit(False))).count()


def _any(conds):
    out = conds[0]
    for c in conds[1:]:
        out = out | c
    return out


def check_schema(df, rule):
    actual = [c for c in df.columns if not c.startswith("_")]
    missing = [c for c in EXPECTED_RAW_COLUMNS if c not in actual]
    extra = [c for c in actual if c not in EXPECTED_RAW_COLUMNS]
    return len(missing) + len(extra)


def check_row_count_min(df, rule):
    return 1 if df.count() < rule["min_rows"] else 0


def check_not_null(df, rule):
    conds = []
    for c in _cols(rule):
        cond = F.col(c).isNull()
        if rule.get("treat_blank_as_null", False):
            cond = cond | (F.trim(F.col(c)) == "")
        conds.append(cond)
    return _count(df, _any(conds))


def check_unique(df, rule):
    cols = rule["columns"]
    dup_keys = df.groupBy(*cols).count().filter(F.col("count") > 1).select(*cols)
    return df.join(dup_keys, on=cols, how="left_semi").count()


def check_allowed_values(df, rule):
    conds = [~F.coalesce(F.col(c).isin(rule["values"]), F.lit(False)) for c in rule["columns"]]
    return _count(df, _any(conds))


def check_int_range(df, rule):
    conds = []
    for c in rule["columns"]:
        # try_cast to INT is null for "abc" and "1.5", so non-integers fail.
        v = F.expr(f"try_cast(trim(`{c}`) AS INT)")
        conds.append(v.isNull() | (v < rule["min"]) | (v > rule["max"]))
    return _count(df, _any(conds))


def check_numeric_range(df, rule):
    conds = []
    for c in rule["columns"]:
        v = F.expr(f"try_cast(trim(`{c}`) AS DOUBLE)")
        conds.append(v.isNull() | (v < rule["min"]) | (v > rule["max"]))
    return _count(df, _any(conds))


def check_expression(df, rule):
    return _count(df, F.expr(rule["failing_condition"]))


RULE_TYPES = {
    "schema": check_schema,
    "row_count_min": check_row_count_min,
    "not_null": check_not_null,
    "unique": check_unique,
    "allowed_values": check_allowed_values,
    "int_range": check_int_range,
    "numeric_range": check_numeric_range,
    "expression": check_expression,
}
