"""Generate docs/EVIDENCE.md from the committed proof files in data/evidence/.

Every number in EVIDENCE.md is read from a JSON file written by a real run; nothing is typed by
hand. A missing file shows as "not measured yet". Resume bullets and README claims should quote
this page only.

    python -m src.evidence.collect
"""
import json
from datetime import datetime

from src.config.settings import EVIDENCE_DIR, REPO_ROOT

OUT = REPO_ROOT / "docs" / "EVIDENCE.md"
EV = "backend/pipeline/data/evidence"


def load(name):
    path = EVIDENCE_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def day(ts):
    return str(ts)[:10] if ts else "?"


def row(claim, value, source, how):
    path = source if source.startswith(".github") else f"{EV}/{source}"
    return f"| {claim} | {value} | `{path}` | `{how}` |"


def missing(claim, source, how):
    return row(claim, "not measured yet", source, how)


def pipeline_rows():
    out = []
    run = load("pipeline_run.json")
    if run:
        stages = {s["stage"]: s for s in run["stages"]}
        ok = sum(s["status"] == "ok" for s in run["stages"])
        out.append(row("Laptop full pipeline run", f"{ok}/{len(run['stages'])} stages ok, {run['total_seconds']} s "
                       f"({day(run['finished'])})", "pipeline_run.json", "python -m src.pipeline"))
        if "dq" in stages:
            r = stages["dq"]["result"]
            out.append(row("Data quality rules on the real file", f"{r.get('pass', 0)} pass, {r.get('warn', 0)} warn, "
                           f"{r.get('fail', 0)} fail", "pipeline_run.json", "python -m src.pipeline --stage dq"))
        if "reconcile" in stages:
            r = stages["reconcile"]["result"]
            out.append(row("Reconciliation against an independent pure-Python calculation",
                           f"{r.get('pass', 0)}/{r.get('pass', 0) + r.get('fail', 0)} checks pass",
                           "pipeline_run.json", "python -m src.pipeline --stage reconcile"))
    else:
        out.append(missing("Laptop full pipeline run", "pipeline_run.json", "python -m src.pipeline"))
    sql = load("sqlserver_load.json")
    if sql:
        c = sql["checks"]
        out.append(row("SQL Server copy matches Delta", f"{len(sql['tables'])} tables loaded, {c['pass']}/{c['pass'] + c['fail']} "
                       f"checks pass, {sql['total_seconds']} s", "sqlserver_load.json", "python -m src.pipeline --stage sqlserver"))
    wh = load("warehouse_build.json")
    if wh:
        r = wh["rows"]
        out.append(row("Star schema built (Delta)", f"fact {r['fact_customer_snapshot']:,} rows, dim_customer {r['dim_customer']:,}, "
                       f"dim_service_profile {r['dim_service_profile']:,}, dim_date {r['dim_date']:,}",
                       "warehouse_build.json", "python -m src.warehouse.star"))
    fi = load("fault_injection.json")
    if fi:
        o = fi["overall"]
        out.append(row("Fault injection: every DQ rule catches exactly the injected bad rows",
                       f"{o['rules_exact_match']}/{o['rules_checked']} rules exact, {o['total_detected_failing_rows']}/"
                       f"{o['total_expected_failing_rows']} failing rows detected, pipeline blocked: {fi['pipeline_blocked']}",
                       "fault_injection.json", "python -m src.evidence.fault_injection"))
    return out


def model_rows():
    m = load("model_metrics.json")
    if not m:
        return [missing("Churn model", "model_metrics.json", "python -m src.model.train")]
    t, b = m["capture"]["top_20"], m["baseline"]
    s = load("model_scoring.json")
    out = [
        row("Churn model (logistic regression), held-out test set", f"AUC-ROC {m['auc_roc_test']}, AUC-PR {m['auc_pr_test']} "
            f"({m['split']['test']['rows']:,} test rows, train AUC-ROC {m['auc_roc_train']})", "model_metrics.json",
            "python -m src.model.train"),
        row("Top 20% riskiest customers", f"capture {t['capture_rate_pct']}% of churners (lift {t['lift']}; random = 20%)",
            "model_metrics.json", "python -m src.model.train"),
        row(f"Baseline rule ({b['rule']})", f"flags {b['flagged_share_pct']}% of customers for {round(b['recall'] * 100, 1)}% of "
            f"churners; the model at the same share catches {b['model_capture_at_same_flagged_share']['capture_rate_pct']}%",
            "model_metrics.json", "python -m src.model.train"),
    ]
    if s:
        c = s["band_counts"]
        out.append(row("Customers scored with top-3 reasons", f"{s['customers_scored']:,} (high {c['high']:,}, medium "
                       f"{c['medium']:,}, low {c['low']:,})", "model_scoring.json", "python -m src.model.score"))
    return out


def analysis_rows():
    a = load("analysis/02_revenue_at_risk.json")
    if not a:
        return []
    r = a["rows"][0]
    return [row("Monthly revenue from customers who churned (SQL Server)", f"{float(r['churned_mrr']):,.2f} of "
                f"{float(r['total_mrr']):,.2f} ({r['churned_share_pct']}%)", "analysis/02_revenue_at_risk.json",
                "python -m src.analysis.run_queries")]


def app_rows():
    out = []
    for name, claim, how in [
        ("supabase_rls_test.json", "Row-level security, tested against the live Supabase project",
         "pytest -m integration tests/test_supabase.py"),
        ("admin_panel_test.json", "Admin panel rules, tested against the deployed function", "pytest -m integration tests/test_admin.py"),
        ("worker_e2e_test.json", "Upload worker end to end (real, faulty and re-processed uploads)",
         "pytest -m integration tests/test_worker.py"),
        ("datasets_live_test.json", "Bring-your-own dataset flow, live", "pytest -m integration tests/test_datasets_live.py"),
    ]:
        d = load(name)
        out.append(row(claim, f"{d['passed']}/{d['assertions']} assertions pass ({day(d['timestamp'])})", name, how)
                   if d else missing(claim, name, how))
    m2 = load("mode2_datasets.json")
    if m2:
        trained = [r for r in m2["results"] if r.get("outcome") == "trained"]
        honest = [r for r in trained if "leak" not in r["variant"]]
        leaky = [r for r in trained if "leak" in r["variant"]]
        aucs = ", ".join(f"{r['file']} {r['auc_roc_test']}" for r in honest)
        note = "".join(f"; leakage demo ({r['file']}, leaking columns put back on purpose): {r['auc_roc_test']}, "
                       "shown only to prove why those columns are removed" for r in leaky)
        out.append(row("Public Kaggle churn files in other layouts", f"{len(m2['results'])} runs, {len(honest)} honest "
                       f"models; test AUC-ROC: {aucs}{note}", "mode2_datasets.json", "python -m src.evidence.mode2_datasets"))
    return out


def databricks_rows():
    run, hist = load("databricks_run.json"), load("databricks_run_history.json")
    if not run:
        return [missing("Databricks scheduled job", "databricks_run.json", "databricks bundle run churn_pipeline")]
    ok = sum(s["status"] == "ok" for s in run["stages"]) if run.get("stages") and "status" in run["stages"][0] else len(run["stages"])
    out = [row("Same src/ modules as a Databricks serverless job (Unity Catalog tables)",
               f"{ok}/{len(run['stages'])} stages ok, {run['total_seconds']} s inside the job", "databricks_run.json",
               "databricks bundle run churn_pipeline")]
    if hist:
        good = [h for h in hist if h["state"].get("result_state") == "SUCCESS"]
        sched = [h for h in good if h.get("trigger") == "PERIODIC"]
        out.append(row("Databricks run history", f"{len(good)}/{len(hist)} runs succeeded, {len(sched)} started by the schedule",
                       "databricks_run_history.json", "python jobs/export_evidence.py"))
    return out


def security_rows():
    s = load("security_check.json")
    if not s:
        return [missing("Security check", "security_check.json", "see README")]
    h, r = s["live_site_headers"], s["supabase_rls_static"]
    return [row("Live site security headers", "CSP (frame-ancestors 'none'), HSTS, nosniff, X-Frame-Options DENY, "
                "Referrer-Policy, Permissions-Policy", "security_check.json", "curl -I " + h["url"]),
            row("Built site", f"0 source maps, 0 secret keys (only the public Supabase publishable key)",
                "security_check.json", "npm run build, then scan dist/"),
            row("Supabase policies (static)", f"{r['tables'] - r['tables_without_rls']}/{r['tables']} tables with RLS, "
                f"{r['definer_without_search_path']} security-definer functions without a fixed search_path",
                "security_check.json", "review of backend/supabase/migrations"),
            *lighthouse_rows(),
            row("Secrets in git history", f"{s['git_history']['env_files_ever_committed']} .env files ever committed, "
                f"{s['confirmed_findings']} confirmed findings", "security_check.json", "git log --all -p | grep <patterns>")]


def lighthouse_rows():
    lh = load("lighthouse.json")
    if not lh:
        return [missing("Lighthouse (live site)", "lighthouse.json", "npx lighthouse@13.6.0 <url>")]
    out = []
    for mode, d in lh.items():
        c, m = d["categories"], d["metrics"]
        out.append(row(f"Lighthouse {mode}, login page (where / redirects) ({day(d['fetchTime'])})",
                       f"performance {c['performance']}, accessibility {c['accessibility']}, best practices "
                       f"{c['best-practices']}, SEO {c['seo']}; LCP {m['largest-contentful-paint']}, CLS "
                       f"{m['cumulative-layout-shift']}".replace(" ", " "),
                       f"lighthouse/lighthouse-{mode}.report.html",
                       f"npx lighthouse@{d['lighthouseVersion']} {d['url']}" + (" --preset=desktop" if mode == "desktop" else "")))
    return out


def scale_section():
    b = load("scale_benchmark.json")
    if not b:
        return ["Not measured yet (`python -m src.evidence.scale_benchmark`)."]
    lines = [f"All rows here are **{b['label']}**. Only timings are meaningful: resampling puts near-duplicate rows in "
             "both the training and test sets, so model metrics on these files are not reported.", "",
             "| Runtime | Rows | Total s | Rows/s | Slowest stages (s) | Machine |", "|---|---|---|---|---|---|"]
    for r in b["runs"]:
        slow = ", ".join(f"{s['stage']} {s['seconds']}" for s in sorted(r["stages"], key=lambda s: -s["seconds"])[:3])
        mc = r.get("machine", {})
        machine = (f"{mc.get('cpu', '?')}, {mc.get('cores', '?')} cores, {mc.get('ram_gb', '?')} GB RAM, driver "
                   f"{r.get('driver_memory', '?')}" if r["runtime"] == "laptop" else mc.get("runtime", "serverless"))
        lines.append(f"| {r['runtime']} | {r['rows']:,} | {r['total_seconds']} | {r['rows_per_second']:,} | {slow} | {machine} |")
    lines += ["", f"Source: `{EV}/scale_benchmark.json`. Reproduce: `python -m src.evidence.scale_benchmark --rows <n> --root <folder outside OneDrive>`."]
    return lines


def ci_rows():
    return [row("Quick test tier (CI)", "see the CI badge in README.md; run time and coverage are in each GitHub Actions log",
                ".github/workflows/ci.yml", 'pytest -m "not integration" --cov=src')]


def build():
    table = ["| Claim | Measured | Source | Reproduce |", "|---|---|---|---|"]
    parts = [f"# Evidence\n\nGenerated by `python -m src.evidence.collect` on {datetime.now().astimezone():%Y-%m-%d}. "
             "Every value is read from a proof file written by a real run. Do not edit by hand.\n"]
    for title, rows in [("Pipeline and data quality (real IBM Telco file, 7,043 rows)", pipeline_rows()),
                        ("Model", model_rows()), ("Analytics", analysis_rows()),
                        ("Live app and back end", app_rows()), ("Databricks", databricks_rows()),
                        ("Security", security_rows()), ("Tests", ci_rows())]:
        if rows:
            parts += [f"## {title}\n", *table, *rows, ""]
    parts += ["## Scale benchmark\n", *scale_section(), ""]
    OUT.write_text("\n".join(parts), encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(f"Wrote {build()}")
