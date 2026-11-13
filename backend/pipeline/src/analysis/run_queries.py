import argparse
import logging
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.config.settings import EVIDENCE_DIR, PROJECT_ROOT, REPO_ROOT, SNAPSHOT_MONTH
from src.config.sqlserver import jdbc_options, jdbc_url
from src.ingestion.spark_session import get_spark
from src.model.report import update_insights
from src.output.evidence import write_evidence

logger = logging.getLogger(__name__)

SQL_DIR = PROJECT_ROOT / "sql" / "analysis"
OUT_DIR = EVIDENCE_DIR / "analysis"
INSIGHTS_PATH = REPO_ROOT / "docs" / "INSIGHTS.md"
MIGRATE_DECLARE = re.compile(r"(DECLARE\s+@migrate_pct\s+DECIMAL\(5,2\)\s*=\s*)[0-9.]+(\s*;)", re.IGNORECASE)

QUESTIONS = {
    "01_churn_overview": "How many customers do we have and what share churned?",
    "02_revenue_at_risk": "How much monthly recurring revenue is lost to churn?",
    "03_contract_internet_matrix": "Which contract and internet service combinations churn the most?",
    "04_tenure_cohorts": "When in the customer lifetime does churn happen, and what does it cost?",
    "05_addon_effect": "Do internet customers with more add-on services churn less?",
    "06_payment_method": "Which payment methods are linked to churn?",
    "07_high_risk_profiles": "Which service segments contain the most churned customers?",
    "08_what_if_contract_migration": "What if some month-to-month customers moved to one-year contracts?",
}


# ---- running the queries ----

def parse_migrate_pct(value):
    try:
        pct = Decimal(str(value))
    except Exception:
        raise ValueError(f"--migrate-pct must be a number, got {value!r}") from None
    if not pct.is_finite() or not Decimal(0) <= pct <= Decimal(100):
        raise ValueError(f"--migrate-pct must be between 0 and 100, got {value!r}")
    return pct


def set_migrate_pct(sql, pct):
    new_sql, count = MIGRATE_DECLARE.subn(lambda m: f"{m.group(1)}{pct}{m.group(2)}", sql)
    if count != 1:
        raise ValueError("Expected exactly one DECLARE @migrate_pct DECIMAL(5,2) = n; line")
    return new_sql


def _value(rs, i, type_name):
    value = rs.getObject(i)
    if value is None:
        return None
    if type_name in ("decimal", "numeric", "money", "smallmoney"):
        return Decimal(str(value))
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)  # java dates and other objects: toString is ISO


def _read_result_set(rs):
    meta = rs.getMetaData()
    columns = [(meta.getColumnLabel(i), meta.getColumnTypeName(i).lower()) for i in range(1, meta.getColumnCount() + 1)]
    rows = []
    while rs.next():
        rows.append({name: _value(rs, i, t) for i, (name, t) in enumerate(columns, start=1)})
    return rows


def query_rows(spark, database, sql):
    """Run one T-SQL batch through the JVM driver and return its result set as a list of dicts."""
    jvm = spark._jvm
    props = jvm.java.util.Properties()
    for key, value in jdbc_options().items():
        if key != "driver":
            props.setProperty(key, value)
    conn = jvm.com.microsoft.sqlserver.jdbc.SQLServerDriver().connect(jdbc_url(database), props)
    try:
        stmt = conn.createStatement()
        try:
            # NOCOUNT stops DECLARE/SET row counts from showing up before the SELECT result.
            has_result = stmt.execute("SET NOCOUNT ON;\n" + sql)
            if not has_result:
                raise RuntimeError("Query returned no result set")
            rs = stmt.getResultSet()
            try:
                return _read_result_set(rs)
            finally:
                rs.close()
        finally:
            stmt.close()
    finally:
        conn.close()


def run_analysis(spark, database="churn_db", sql_dir=SQL_DIR, out_dir=OUT_DIR, migrate_pct=None):
    results = {}
    run_ts = datetime.now().astimezone().isoformat()
    for path in sorted(Path(sql_dir).glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        if migrate_pct is not None and "@migrate_pct" in sql:
            sql = set_migrate_pct(sql, migrate_pct)
        rows = query_rows(spark, database, sql)
        results[path.stem] = rows
        write_evidence(f"{path.stem}.json", {
            "query": path.name, "database": database, "run_timestamp": run_ts, "rows": rows,
        }, evidence_dir=out_dir)
        logger.info("%s: %d rows", path.stem, len(rows))
    return results


# ---- generating INSIGHTS.md ----

def _n(x):
    return f"{int(x):,}"


def _money(x):
    return f"{Decimal(x):,.2f}"


def _table(rows):
    if not rows:
        return "_No rows._"
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        lines.append("| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def _cond(rows, key, value):
    return next(r for r in rows if r[key] == value)


def _extreme(rows, key, highest=True):
    return (max if highest else min)(rows, key=lambda r: Decimal(r[key]))


def text_overview(res):
    r = res["01_churn_overview"][0]
    return (f"In snapshot month {r['snapshot_month']}, {_n(r['churned'])} of {_n(r['customers'])} customers "
            f"churned, a churn rate of {r['churn_rate_pct']}%.")


def text_revenue(res):
    r = res["02_revenue_at_risk"][0]
    return (f"Churned customers took {_money(r['churned_mrr'])} of {_money(r['total_mrr'])} monthly recurring "
            f"revenue ({r['churned_share_pct']}%). Churned customers paid {_money(r['avg_charge_churned'])} "
            f"per month on average against {_money(r['avg_charge_retained'])} for retained customers.")


def text_matrix(res):
    rows = res["03_contract_internet_matrix"]
    top, low = rows[0], _extreme(rows, "churn_rate_pct", highest=False)
    return (f"Among cells with at least 50 customers, {top['contract']} contracts with {top['internet_service']} "
            f"internet churn most ({top['churn_rate_pct']}%, {_n(top['churned'])} of {_n(top['customers'])}). "
            f"The lowest is {low['contract']} with {low['internet_service']} internet ({low['churn_rate_pct']}%, "
            f"{_n(low['churned'])} of {_n(low['customers'])}).")


def text_tenure(res):
    rows = res["04_tenure_cohorts"]
    top, first = _extreme(rows, "churn_rate_pct"), rows[0]
    return (f"The {top['tenure_band']} month tenure band churns most ({top['churn_rate_pct']}%) and loses "
            f"{_money(top['mrr_lost'])} per month. Customers in the {first['tenure_band']} month band alone "
            f"are {first['cumulative_churned_share_pct']}% of all churned customers.")


def text_addon(res):
    rows = res["05_addon_effect"]
    first, last, low = rows[0], rows[-1], _extreme(rows, "churn_rate_pct", highest=False)
    return (f"Among internet customers, churn is {first['churn_rate_pct']}% with {first['addon_service_count']} "
            f"add-ons and {last['churn_rate_pct']}% with {last['addon_service_count']} add-ons. The lowest rate "
            f"is {low['churn_rate_pct']}% at {low['addon_service_count']} add-ons ({_n(low['customers'])} customers).")


def text_payment(res):
    rows = res["06_payment_method"]
    top, low = rows[0], rows[-1]
    return (f"{top['payment_method']} has the highest churn rate ({top['churn_rate_pct']}%) and accounts for "
            f"{top['share_of_churned_pct']}% of all churned customers, losing {_money(top['mrr_lost'])} per month. "
            f"{low['payment_method']} has the lowest ({low['churn_rate_pct']}%).")


def text_profiles(res):
    rows = res["07_high_risk_profiles"]
    top = rows[0]
    total = sum(int(r["churned"]) for r in rows)
    return (f"The segment with the most churned customers is {top['contract']}, {top['internet_service']} internet, "
            f"{top['payment_method']}, tech support {top['tech_support']}, online security {top['online_security']}: "
            f"{_n(top['churned'])} of {_n(top['customers'])} churned ({top['churn_rate_pct']}%). "
            f"The top {len(rows)} segments together hold {_n(total)} of all churned customers.")


def text_whatif(res):
    r = res["08_what_if_contract_migration"][0]
    return (f"If {r['migrate_pct']}% of the {_n(r['mtm_customers'])} month-to-month customers "
            f"({r['customers_migrated']} customers) moved to one-year contracts, {r['churned_avoided']} fewer "
            f"customers would churn, retaining about {_money(r['monthly_revenue_retained'])} of monthly revenue. "
            f"Assumptions: they would churn at the one-year rate ({r['one_year_churn_rate_pct']}% against "
            f"{r['mtm_churn_rate_pct']}% for month-to-month), selection effects are ignored, and revenue uses the "
            f"average monthly charge of month-to-month churners ({_money(r['mtm_churner_avg_charge'])}). "
            f"Customers avoided are shown to one decimal; revenue is computed from unrounded values. "
            f"Illustrative, not causal.")


SECTION_TEXT = {
    "01_churn_overview": text_overview,
    "02_revenue_at_risk": text_revenue,
    "03_contract_internet_matrix": text_matrix,
    "04_tenure_cohorts": text_tenure,
    "05_addon_effect": text_addon,
    "06_payment_method": text_payment,
    "07_high_risk_profiles": text_profiles,
    "08_what_if_contract_migration": text_whatif,
}


def recommendations(res):
    matrix, tenure, pay = res["03_contract_internet_matrix"], res["04_tenure_cohorts"], res["06_payment_method"]
    mtm = [r for r in matrix if r["contract"] == "Month-to-month"]
    mtm_top = _extreme(mtm, "churn_rate_pct") if mtm else matrix[0]
    first, top_pay = tenure[0], pay[0]
    safest = _extreme(pay, "churn_rate_pct", highest=False)
    whatif = res["08_what_if_contract_migration"][0]
    return [
        (f"Target month-to-month customers first: {mtm_top['contract']} with {mtm_top['internet_service']} internet "
         f"churns at {mtm_top['churn_rate_pct']}% (query 3)."),
        (f"Focus on the first year: the {first['tenure_band']} month band churns at {first['churn_rate_pct']}% and "
         f"holds {first['cumulative_churned_share_pct']}% of all churned customers (query 4)."),
        (f"Move {top_pay['payment_method']} customers toward automatic payment: they churn at "
         f"{top_pay['churn_rate_pct']}% against {safest['churn_rate_pct']}% for {safest['payment_method']} (query 6)."),
        (f"Size contract offers with the what-if in query 8: at {whatif['migrate_pct']}% migration it is worth about "
         f"{_money(whatif['monthly_revenue_retained'])} per month under its stated assumptions."),
    ]


def write_insights(results, path=INSIGHTS_PATH, sql_dir=SQL_DIR):
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# Churn insights",
        "",
        f"Generated on {generated}.",
        "",
        f"Snapshot month {SNAPSHOT_MONTH} is assigned; the source file (IBM Telco, 7,043 customers) is undated.",
        "",
        "Generated by `python -m src.analysis.run_queries`; do not edit by hand.",
        "",
    ]
    for name, fill in SECTION_TEXT.items():
        if name not in results:
            continue
        sql = (Path(sql_dir) / f"{name}.sql").read_text(encoding="utf-8").strip()
        lines += [
            f"## {QUESTIONS[name]}", "", fill(results), "", _table(results[name]), "",
            "<details>", f"<summary>SQL: {name}.sql</summary>", "", "```sql", sql, "```", "", "</details>", "",
        ]
    lines += ["## Recommendations", ""]
    lines += [f"- {text}" for text in recommendations(results)]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


SUMMARY = {
    "01_churn_overview": lambda r: f"{_n(r[0]['churned'])}/{_n(r[0]['customers'])} churned ({r[0]['churn_rate_pct']}%)",
    "02_revenue_at_risk": lambda r: f"churned MRR {_money(r[0]['churned_mrr'])} of {_money(r[0]['total_mrr'])}",
    "08_what_if_contract_migration": lambda r: f"{r[0]['migrate_pct']}% -> {_money(r[0]['monthly_revenue_retained'])} retained",
}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--migrate-pct", default="10", help="share of month-to-month customers migrated (0-100)")
    args = parser.parse_args()
    pct = parse_migrate_pct(args.migrate_pct)
    start = time.perf_counter()
    spark = get_spark()
    try:
        results = run_analysis(spark, migrate_pct=pct)
    finally:
        spark.stop()
    path = write_insights(results)
    update_insights(path)  # write_insights rewrites the file; re-add the generated model section
    for name, rows in results.items():
        extra = SUMMARY[name](rows) if name in SUMMARY else ""
        print(f"{name}: {len(rows)} rows {extra}".rstrip())
    print(f"Wrote {path}")
    print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
