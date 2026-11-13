import csv
import socket
from decimal import Decimal

import pytest

from src.analysis.run_queries import (
    SQL_DIR, parse_migrate_pct, run_analysis, set_migrate_pct, write_insights,
)
from src.config.settings import RAW_CSV


def sqlserver_up():
    try:
        with socket.create_connection(("localhost", 1433), timeout=2):
            return True
    except OSError:
        return False


def fake_results():
    return {
        "01_churn_overview": [dict(snapshot_month="2026-10", customers=100, churned=25, churn_rate_pct=Decimal("25.00"))],
        "02_revenue_at_risk": [dict(total_mrr=Decimal("1000.00"), churned_mrr=Decimal("300.50"),
                                    churned_share_pct=Decimal("30.05"), avg_charge_churned=Decimal("70.10"),
                                    avg_charge_retained=Decimal("60.20"))],
        "03_contract_internet_matrix": [
            dict(contract="Month-to-month", internet_service="Fiber optic", customers=60, churned=33,
                 churn_rate_pct=Decimal("55.00"), rate_rank=1),
            dict(contract="Two year", internet_service="No", customers=50, churned=1,
                 churn_rate_pct=Decimal("2.00"), rate_rank=2)],
        "04_tenure_cohorts": [
            dict(tenure_band="0-12", customers=40, churned=20, churn_rate_pct=Decimal("50.00"),
                 mrr_lost=Decimal("1400.00"), cumulative_churned_share_pct=Decimal("80.00")),
            dict(tenure_band="13-24", customers=60, churned=5, churn_rate_pct=Decimal("8.33"),
                 mrr_lost=Decimal("300.00"), cumulative_churned_share_pct=Decimal("100.00"))],
        "05_addon_effect": [
            dict(addon_service_count=0, customers=10, churned=5, churn_rate_pct=Decimal("50.00")),
            dict(addon_service_count=6, customers=10, churned=1, churn_rate_pct=Decimal("10.00"))],
        "06_payment_method": [
            dict(payment_method="Electronic check", customers=50, churned=20, churn_rate_pct=Decimal("40.00"),
                 mrr_lost=Decimal("1500.00"), share_of_churned_pct=Decimal("80.00")),
            dict(payment_method="Mailed check", customers=50, churned=5, churn_rate_pct=Decimal("10.00"),
                 mrr_lost=Decimal("300.00"), share_of_churned_pct=Decimal("20.00"))],
        "07_high_risk_profiles": [
            dict(contract="Month-to-month", internet_service="DSL", payment_method="Mailed check",
                 tech_support="No", online_security="No", customers=40, churned=12, churn_rate_pct=Decimal("30.00"))],
        "08_what_if_contract_migration": [
            dict(migrate_pct=Decimal("10.00"), mtm_customers=3875, mtm_churn_rate_pct=Decimal("42.71"),
                 one_year_churn_rate_pct=Decimal("11.27"), mtm_churner_avg_charge=Decimal("73.02"),
                 customers_migrated=Decimal("387.5"), churned_avoided=Decimal("121.8"),
                 monthly_revenue_retained=Decimal("8896.00"))],
    }


# ---- unit ----

@pytest.mark.parametrize("value", ["0", "10", "100", "12.5", 10])
def test_migrate_pct_accepts_numbers_in_range(value):
    assert Decimal(0) <= parse_migrate_pct(value) <= Decimal(100)


@pytest.mark.parametrize("value", ["-1", "100.5", "abc", "", "nan", "inf", "10; DROP TABLE x"])
def test_migrate_pct_rejects_bad_input(value):
    with pytest.raises(ValueError):
        parse_migrate_pct(value)


def test_set_migrate_pct_replaces_the_declare_default():
    sql = (SQL_DIR / "08_what_if_contract_migration.sql").read_text(encoding="utf-8")
    new = set_migrate_pct(sql, parse_migrate_pct("12.5"))
    assert "DECLARE @migrate_pct DECIMAL(5,2) = 12.5;" in new
    with pytest.raises(ValueError):
        set_migrate_pct("SELECT 1", Decimal(5))


def test_insights_has_headings_and_numbers_from_results(tmp_path):
    path = write_insights(fake_results(), tmp_path / "INSIGHTS.md")
    text = path.read_text(encoding="utf-8")
    assert "do not edit by hand" in text
    assert "Snapshot month 2026-10 is assigned" in text
    assert text.count("\n## ") == 9  # eight questions + Recommendations
    assert "## Recommendations" in text
    assert "25 of 100 customers churned, a churn rate of 25.00%" in text
    assert "300.50 of 1,000.00 monthly recurring revenue (30.05%)" in text
    assert "Electronic check has the highest churn rate (40.00%)" in text
    assert "8,896.00" in text and "Illustrative, not causal" in text
    assert text.count("<details>") == 8 and "SELECT" in text
    assert "| 0-12 | 40 | 20 | 50.00 |" in text


# ---- integration: read-only against the real churn_db ----

@pytest.mark.integration
@pytest.mark.skipif(not RAW_CSV.exists(), reason="raw CSV missing")
@pytest.mark.skipif(not sqlserver_up(), reason="SQL Server not reachable on localhost:1433")
def test_analysis_queries_match_the_source_csv(spark, tmp_path):
    results = run_analysis(spark, out_dir=tmp_path)
    assert len(results) == 8
    assert len(list(tmp_path.glob("*.json"))) == 8

    overview = results["01_churn_overview"][0]
    assert overview["customers"] == 7043 and overview["churned"] == 1869

    with open(RAW_CSV, newline="", encoding="utf-8") as f:
        churned_mrr = sum(Decimal(r["MonthlyCharges"]) for r in csv.DictReader(f) if r["Churn"] == "Yes")
    assert results["02_revenue_at_risk"][0]["churned_mrr"].quantize(Decimal("0.01")) == churned_mrr.quantize(Decimal("0.01"))
