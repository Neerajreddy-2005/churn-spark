import csv
from collections import Counter

import pytest

from src.config.settings import RAW_CSV
from src.evidence.fault_injection import inject_faults, run_fault_injection

needs_csv = pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV missing")


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def small_csv(tmp_path):
    lines = RAW_CSV.read_text(encoding="utf-8").splitlines(keepends=True)[:301]  # header + 300 rows
    path = tmp_path / "small.csv"
    path.write_text("".join(lines), encoding="utf-8")
    return path


# ---- unit (no Spark) ----

@needs_csv
def test_defects_land_on_distinct_rows_and_skip_blank_total_charges(small_csv, tmp_path):
    plan = inject_faults(small_csv, tmp_path / "out.csv", n_per_rule=3, seed=1)
    src, out = read(small_csv), read(tmp_path / "out.csv")
    assert len(out) == len(src)

    changed = {i for i, (a, b) in enumerate(zip(src, out)) if a != b}
    touched = [i for idx in plan["rows"].values() for i in idx]
    assert len(touched) == len(set(touched))  # a row gets at most one defect
    assert changed == set(touched)
    for i, row in enumerate(src):
        if row["TotalCharges"].strip() == "":
            assert out[i] == row


@needs_csv
def test_expected_counts_are_exact_for_a_hand_checkable_case(small_csv, tmp_path):
    n = 3
    plan = inject_faults(small_csv, tmp_path / "out.csv", n_per_rule=n, seed=1)
    src, out = read(small_csv), read(tmp_path / "out.csv")

    for rule_id in plan["targeted"]:
        assert plan["expected"][rule_id] == (2 * n if rule_id == "dq04" else n)
        assert plan["injected"][rule_id] == n
    baseline = sum(1 for r in src if r["TotalCharges"].strip() == "" and r["tenure"] == "0")
    assert plan["expected"]["dq10"] == baseline  # untargeted: keeps the real-data baseline

    ids = Counter(r["customerID"] for r in out)
    assert sum(c for c in ids.values() if c > 1) == 2 * n  # dq04 counts rows, not groups
    blank_ids = [out[i]["customerID"] for i in plan["rows"]["dq03"]]
    assert all(v.strip() == "" and v != "" for v in blank_ids)
    assert len(set(blank_ids)) == n  # distinct, so dq03 rows do not create duplicates
    assert all(out[i]["MultipleLines"] == "Sometimes" and out[i]["PhoneService"] == "Yes"
               for i in plan["rows"]["dq06a"])
    assert all(out[i]["TotalCharges"] == "" for i in plan["rows"]["dq14"])


@needs_csv
def test_same_seed_gives_same_file(small_csv, tmp_path):
    inject_faults(small_csv, tmp_path / "a.csv", n_per_rule=2, seed=7)
    inject_faults(small_csv, tmp_path / "b.csv", n_per_rule=2, seed=7)
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()


def test_too_few_eligible_rows_is_an_error(tmp_path):
    src = tmp_path / "tiny.csv"
    header = "customerID,gender,SeniorCitizen,Partner,Dependents,tenure,PhoneService,MultipleLines,InternetService,OnlineSecurity,OnlineBackup,DeviceProtection,TechSupport,StreamingTV,StreamingMovies,Contract,PaperlessBilling,PaymentMethod,MonthlyCharges,TotalCharges,Churn"
    src.write_text(header + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        inject_faults(src, tmp_path / "out.csv", n_per_rule=1)


# ---- integration ----

@pytest.mark.integration
@needs_csv
def test_every_rule_detects_exactly_what_was_injected(spark, tmp_path):
    report = run_fault_injection(spark, n_per_rule=5, seed=42, evidence_dir=tmp_path)
    assert report["pipeline_blocked"] is True
    assert all(t["exact_match"] for t in report["rules"]), [t for t in report["rules"] if not t["exact_match"]]
    assert report["overall"]["rules_exact_match"] == report["overall"]["rules_checked"]
    assert (tmp_path / "fault_injection.json").exists()
