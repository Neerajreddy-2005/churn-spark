import json

import pytest

from src.config.settings import DQ_RULES_PATH, EXPECTED_RAW_COLUMNS, RAW_CSV
from src.ingestion.bronze_loader import load_bronze
from src.quality.report import RESULT_SCHEMA
from src.quality.rules import RULE_TYPES
from src.quality.runner import DataQualityError, enforce, load_rules, rule_status, run_checks, run_dq


def make_df(spark, cols, rows):
    return spark.createDataFrame(rows, cols)


def fails(spark, rule_type, data, **rule):
    """Run one rule over a single-column DataFrame named v (or custom cols)."""
    cols = list(data[0].keys())
    df = make_df(spark, cols, [tuple(r[c] for c in cols) for r in data])
    rule.setdefault("columns", ["v"])
    return RULE_TYPES[rule_type](df, rule)


def rows(*vals):
    return [{"v": v} for v in vals]


def rule(id="r", type="not_null", action="fail", columns=None, **kw):
    return {"id": id, "description": id, "type": type, "columns": columns or ["v"],
            "action": action, **kw}


def test_schema(spark):
    ok = make_df(spark, EXPECTED_RAW_COLUMNS + ["_ingest_ts"], [tuple(["x"] * 22)])
    assert RULE_TYPES["schema"](ok, {"columns": []}) == 0
    bad = make_df(spark, EXPECTED_RAW_COLUMNS[1:] + ["extra"], [tuple(["x"] * 21)])
    assert RULE_TYPES["schema"](bad, {"columns": []}) == 2  # 1 missing + 1 extra


def test_row_count_min(spark):
    df = make_df(spark, ["v"], [("a",), ("b",)])
    assert RULE_TYPES["row_count_min"](df, {"min_rows": 2}) == 0
    assert RULE_TYPES["row_count_min"](df, {"min_rows": 3}) == 1


def test_not_null(spark):
    assert fails(spark, "not_null", rows("a", "b")) == 0
    assert fails(spark, "not_null", rows("a", None, " ")) == 1  # blank not counted by default


def test_not_null_star_ignores_blank(spark):
    df = make_df(spark, EXPECTED_RAW_COLUMNS, [tuple(["x"] * 20 + [" "]), tuple(["x"] * 20 + [None])])
    assert RULE_TYPES["not_null"](df, {"columns": ["*"]}) == 1


def test_not_null_treat_blank_as_null(spark):
    assert fails(spark, "not_null", rows("a", None, " ", ""), treat_blank_as_null=True) == 3


def test_unique_counts_rows_not_groups(spark):
    assert fails(spark, "unique", rows("a", "b")) == 0
    assert fails(spark, "unique", rows("a", "a", "b")) == 2


def test_allowed_values(spark):
    assert fails(spark, "allowed_values", rows("Yes", "No"), values=["Yes", "No"]) == 0
    assert fails(spark, "allowed_values", rows("Yes", "Maybe", None), values=["Yes", "No"]) == 2


def test_allowed_values_any_column(spark):
    data = [{"a": "Yes", "b": "No"}, {"a": "Yes", "b": "x"}]
    assert fails(spark, "allowed_values", data, columns=["a", "b"], values=["Yes", "No"]) == 1


def test_int_range(spark):
    kw = {"min": 0, "max": 72}
    assert fails(spark, "int_range", rows("0", "72", "10"), **kw) == 0
    assert fails(spark, "int_range", rows("0", "73", "-1", "abc", "1.5", None), **kw) == 5


def test_numeric_range(spark):
    kw = {"min": 18.25, "max": 118.75}
    assert fails(spark, "numeric_range", rows("18.25", "118.75", "50"), **kw) == 0
    assert fails(spark, "numeric_range", rows("18.24", "118.76", "abc", " ", "50"), **kw) == 4


def test_expression(spark):
    cond = "v = 'bad'"
    assert fails(spark, "expression", rows("ok"), failing_condition=cond) == 0
    assert fails(spark, "expression", rows("bad", "bad", None, "ok"), failing_condition=cond) == 2


def test_threshold_direction(spark):
    df = make_df(spark, ["v"], [("a",), ("bad",)])
    r = rule(type="expression", failing_condition="v = 'bad'", max_failing_rate=0.5)
    assert run_checks(df, [r], "x")[0]["status"] == "pass"  # exactly 50% is allowed
    df3 = make_df(spark, ["v"], [("bad",), ("bad",), ("a",)])
    res = run_checks(df3, [r], "x")[0]
    assert res["status"] == "fail"
    assert res["failing_rows"] == 2 and res["checked_rows"] == 3
    assert res["failing_rate"] == round(2 / 3, 6)
    assert res["threshold"] == 0.5


@pytest.mark.parametrize("action,status", [("fail", "fail"), ("warn", "warn"), ("fix", "warn")])
def test_status_mapping(spark, action, status):
    df = make_df(spark, ["v"], [("bad",)])
    r = rule(type="expression", action=action, failing_condition="v = 'bad'")
    res = run_checks(df, [r], "run-1")[0]
    assert res["status"] == status
    assert res["run_id"] == "run-1"
    assert res["columns"] == "v"


def test_enforce():
    enforce([{"rule_id": "a", "status": "pass"}, {"rule_id": "b", "status": "warn"}])
    with pytest.raises(DataQualityError, match="c"):
        enforce([{"rule_id": "b", "status": "warn"}, {"rule_id": "c", "status": "fail"}])


@pytest.mark.parametrize("change,msg", [
    ({"type": "bogus"}, "bogus"),
    ({"action": "explode"}, "explode"),
])
def test_load_rules_rejects_unknown(tmp_path, change, msg):
    bad = {**rule(id="rX"), **change}
    path = tmp_path / "r.json"
    path.write_text(json.dumps([bad]))
    with pytest.raises(ValueError, match="rX.*" + msg):
        load_rules(path)


def test_load_rules_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps([rule(id="d1"), rule(id="d1")]))
    with pytest.raises(ValueError, match="d1"):
        load_rules(path)


def test_run_dq_records_results_even_when_enforce_raises(spark, tmp_path):
    bronze = tmp_path / "bronze"
    raw = [tuple(["x"] * 21)] * 2  # duplicated customerID
    spark.createDataFrame(raw, EXPECTED_RAW_COLUMNS).write.format("delta").save(bronze.as_posix())
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(json.dumps([rule(id="dq04", type="unique", columns=["customerID"])]))
    results_path, report = tmp_path / "dq_results", tmp_path / "out" / "report.json"

    with pytest.raises(DataQualityError, match="dq04"):
        run_dq(spark, bronze, rules_path, results_path, report, run_id="run-xyz")

    data = json.loads(report.read_text())
    assert data["run_id"] == "run-xyz"
    assert data["summary"] == {"pass": 0, "warn": 0, "fail": 1}
    table = spark.read.format("delta").load(results_path.as_posix())
    assert table.schema == RESULT_SCHEMA
    got = table.collect()
    assert [(r.run_id, r.rule_id, r.failing_rows) for r in got] == [("run-xyz", "dq04", 2)]


@pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV not present")
@pytest.mark.integration
def test_real_data_integration(spark, tmp_path):
    bronze = tmp_path / "bronze"
    load_bronze(spark, RAW_CSV, bronze)
    results = run_dq(spark, bronze, DQ_RULES_PATH, tmp_path / "res", tmp_path / "rep.json")
    by_id = {r["rule_id"]: r for r in results}
    config_ids = [r["id"] for r in load_rules(DQ_RULES_PATH)]
    assert [r["rule_id"] for r in results] == config_ids
    assert len(by_id) == len(config_ids)
    assert not [r for r in results if r["status"] == "fail"]
    assert by_id["dq10"]["status"] == "warn" and by_id["dq10"]["failing_rows"] == 11
    assert by_id["dq11"]["failing_rows"] == 0
    assert by_id["dq11b"]["failing_rows"] == 0


def test_tiny_failing_rate_is_not_rounded_to_pass():
    # 1 bad row in 3 million rounds to 0.0 at 6 dp; it must still fail a 0 threshold.
    assert rule_status(1, 3_000_000, 0.0, "fail") == "fail"
    assert rule_status(0, 3_000_000, 0.0, "fail") == "pass"
