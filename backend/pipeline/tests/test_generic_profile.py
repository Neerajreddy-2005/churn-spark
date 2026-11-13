"""Mode 2, step 1: reader, profiler, suggestions and general checks."""
import zipfile

import openpyxl
import pytest

from src.generic.checks import can_train, run_checks
from src.generic.profile import profile_table
from src.generic.reader import MAX_COLUMNS, UnreadableFile, read_table, write_canonical
from src.generic.suggest import positive_value, redact, suggest, words


# ---- reader ----

def test_reader_sniffs_semicolons_and_strips_bom(tmp_path):
    p = tmp_path / "s.csv"
    p.write_bytes("﻿id;Churn;Plan\n1;Yes;Basic\n2;No;Pro\n".encode("utf-8"))
    header, rows, info = read_table(p)
    assert header == ["id", "Churn", "Plan"] and rows == [["1", "Yes", "Basic"], ["2", "No", "Pro"]]
    assert info["delimiter"] == ";"


def test_reader_cleans_headers_and_pads_rows(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("a, ,a\n1,2\n", encoding="utf-8")
    header, rows, _ = read_table(p)
    assert header == ["a", "column 2", "a (2)"] and rows == [["1", "2", ""]]


def test_reader_xlsx_picks_the_data_sheet(tmp_path):
    p = tmp_path / "w.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Data Dict"
    wb.active.append(["Variable", "Meaning"])
    data = wb.create_sheet("Data")
    data.append(["CustomerID", "Churn", "Spend"])
    for i in range(5):
        data.append([i, i % 2, 10.5 + i])
    wb.save(p)
    header, rows, info = read_table(p)
    assert info["sheet"] == "Data" and header == ["CustomerID", "Churn", "Spend"]
    assert rows[0] == ["0", "0", "10.5"] and len(rows) == 5


def test_reader_rejects_zip_bomb_and_bad_files(tmp_path):
    bomb = tmp_path / "b.xlsx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/big.xml", "0" * (61 * 1024 * 1024))
    with pytest.raises(UnreadableFile, match="60 MB"):
        read_table(bomb)
    fake = tmp_path / "f.xlsx"
    fake.write_text("not a zip", encoding="utf-8")
    with pytest.raises(UnreadableFile, match="not a readable"):
        read_table(fake)
    wide = tmp_path / "wide.csv"
    wide.write_text(",".join(f"c{i}" for i in range(MAX_COLUMNS + 1)) + "\n", encoding="utf-8")
    with pytest.raises(UnreadableFile, match="columns"):
        read_table(wide)
    one = tmp_path / "one.csv"
    one.write_text("only\n1\n", encoding="utf-8")
    with pytest.raises(UnreadableFile, match="one column"):
        read_table(one)


def test_canonical_header_never_uses_user_names(tmp_path):
    cols = write_canonical(["a`; drop table x", "b"], [["1", "2"]], tmp_path / "c.csv")
    assert (tmp_path / "c.csv").read_text(encoding="utf-8").splitlines() == ["c0,c1,row", "1,2,1"]
    assert cols[0] == {"key": "c0", "name": "a`; drop table x"}


# ---- suggestions (pure) ----

def col(key, name, kind="number", distinct=10, blank=0, rows=1000, top=None, integer=True):
    return {"key": key, "name": name, "kind": kind, "distinct": distinct, "blank": blank,
            "filled": rows - blank, "top": top or [], "integer": integer}


def bank_like(rows=1000):
    return {"rows": rows, "columns": [
        col("c0", "RowNumber", distinct=rows), col("c1", "CustomerId", distinct=rows),
        col("c2", "Surname", "text", distinct=300), col("c3", "Age", distinct=60),
        col("c4", "Balance", distinct=rows, integer=False),
        col("c5", "Exited", distinct=2, top=[("0", 800), ("1", 200)]),
        col("c6", "Churn Score", distinct=80), col("c7", "Country", "text", distinct=1),
        col("c8", "Zip Code", distinct=400), col("c9", "City", "text", distinct=120),
        col("c10", "Signup Date", "date", distinct=500), col("c11", "Phone Service", "text", distinct=2),
    ]}


def test_suggest_bank_like():
    s = suggest(bank_like())
    assert (s["target"], s["positive"], s["id"]) == ("c5", "1", "c1")
    kinds = {k: e["kind"] for k, e in s["exclude"].items()}
    assert kinds == {"c0": "identifier", "c2": "personal", "c6": "leakage", "c7": "constant",
                     "c8": "code", "c9": "categories", "c10": "date"}
    # a decimal quantity unique on every row is NOT an identifier; "Phone Service" is not personal
    assert "c4" in s["features"] and "c11" in s["features"]
    assert s["hide_values"] == ["c0", "c1", "c2"] and s["is_churn_data"]


def test_two_valued_column_is_not_a_target_without_an_outcome_name():
    p = {"rows": 100, "columns": [col("c0", "Order ID", distinct=100),
                                  col("c1", "Sales Channel", "text", distinct=2, top=[("Online", 50), ("Offline", 50)])]}
    s = suggest(p)
    assert s["target"] is None and not s["is_churn_data"] and "exploration" in s["target_note"]


def test_positive_value_and_words():
    assert positive_value(["No", "Yes"]) == "Yes" and positive_value(["0", "1"]) == "1"
    assert positive_value(["A", "B"]) is None
    assert words("CustomerId") == "customer id" and words("Usage Frequency") == "usage frequency"


def test_redact_hides_values():
    p = {"rows": 2, "columns": [{"key": "c0", "top": [("Smith", 1)]}, {"key": "c1", "top": [("x", 2)]}]}
    out = redact(p, ["c0"])
    assert out["columns"][0]["top"] == [] and out["columns"][0]["values_hidden"]
    assert out["columns"][1]["top"] == [("x", 2)]


# ---- general checks (pure) ----

def review_of(p, **kw):
    s = suggest(p)
    return {"id": s["id"], "target": s["target"], "positive": s["positive"], "features": s["features"], **kw}


def test_checks_pass_and_fail():
    p = bank_like()
    assert can_train(run_checks(p, review_of(p)))
    small = bank_like(rows=150)
    res = {r["rule_id"]: r["status"] for r in run_checks(small, review_of(small))}
    assert res["g04"] == "fail"
    p["columns"][5]["top"] = [("0", 990), ("1", 10)]
    res = {r["rule_id"]: r for r in run_checks(p, review_of(p))}
    assert res["g05"]["status"] == "fail" and "10" in res["g05"]["detail"]
    dup = review_of(p)
    dup["features"] = dup["features"] + [dup["target"]]
    assert {r["rule_id"]: r["status"] for r in run_checks(p, dup)}["g07"] == "fail"


# ---- profile (Spark) ----

def test_profile_table(spark, tmp_path):
    rows = [[str(i), "Yes" if i % 4 == 0 else "No", str(10 + i % 7) if i % 10 else "", f"2024-01-{1 + i % 28:02d}",
             "Gold" if i % 2 else "Basic", f"{i * 1.5}"] for i in range(40)]
    cols = write_canonical(["CustomerID", "Churn", "Calls", "Joined", "Plan", "Spend"], rows, tmp_path / "c.csv")
    p = profile_table(spark, tmp_path / "c.csv", cols)
    by = {c["name"]: c for c in p["columns"]}
    assert p["rows"] == 40
    assert by["Calls"]["kind"] == "number" and by["Calls"]["blank"] == 4 and by["Calls"]["missing_pct"] == 10.0
    assert by["Calls"]["integer"] and by["Calls"]["min"] == 10.0 and by["Calls"]["max"] == 16.0
    assert sum(b["count"] for b in by["Calls"]["histogram"]) == 36
    assert by["Joined"]["kind"] == "date" and by["Plan"]["kind"] == "text"
    assert by["Churn"]["top"] == [("No", 30), ("Yes", 10)] and by["Spend"]["integer"] is False
    s = suggest(p)
    assert (s["target"], s["positive"], s["id"]) == ("c1", "Yes", "c0")


def test_auto_review_only_when_clear():
    from src.integration.dataset_worker import auto_review
    p = bank_like()
    r = auto_review(p, suggest(p))
    assert r and r["auto"] is True and r["target"] == "c5" and r["id"] == "c1"
    small = bank_like(rows=150)  # fails g04 (fewer than 200 rows): ask instead of training
    assert auto_review(small, suggest(small)) is None
    sales = {"rows": 300, "columns": [col("c0", "Order ID", distinct=300, rows=300),
                                      col("c1", "Sales Channel", "text", distinct=2, rows=300,
                                          top=[("Online", 150), ("Offline", 150)])]}
    assert auto_review(sales, suggest(sales)) is None  # no churn column by name: ask
