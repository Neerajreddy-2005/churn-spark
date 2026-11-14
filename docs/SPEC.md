# Churn-Spark Specification

## Goals

- Build an end-to-end batch pipeline: CSV to Bronze to Silver to Gold in Delta Lake, then SQL Server, then Power BI.
- Show a working data quality framework with explicit rules, thresholds and recorded results.
- Show verifiable correctness: every reported number reconciles to the source file.
- Keep the code in plain modules under `src/` so it runs both locally and in Databricks notebooks.
- Make it reproducible on one Windows laptop with free software.
- Build a retention workbench: a telecom retention team uploads a monthly customer CSV, the pipeline validates and scores it, and a public web app shows the DQ report, an at-risk list with reasons, retention action logging, monthly trends and pipeline run history.
- Prove data-role skills with working, demonstrable components (pipeline, data quality, star schema and BI, model, serving app, Databricks Job).

## Non-goals

- No deep learning or model tuning beyond a simple, explainable model.
- No streaming and no orchestration tool (Airflow, ADF). The only schedule is the Databricks Job in Phase 9.
- Not a commercial product: free tiers, single demo organisation, laptop-run batch pipeline.
- No performance claims beyond measured results. The real dataset has about 7,000 rows and runs in seconds; runtime is measured and recorded, not targeted. Scale figures come only from the labelled synthetic benchmark (Phases 9 and 10).

## Design decisions

Each is decided but changeable; changing one means updating this section.

| Decision | Choice | Rationale |
|---|---|---|
| Primary runtime | Local PySpark 3.5.x + delta-spark 3.x on Windows, Java 17 | Free, no quota, works with local SQL Server |
| Secondary runtime | Same `src/` modules as notebooks in Databricks Free Edition (serverless only) | Shows cloud familiarity; Free Edition limits must be checked at the time |
| Databricks role | Same `src/` modules run as a scheduled Databricks Job writing Unity Catalog tables (Phase 9); the live app and SQL Server are fed from the laptop run | Demonstrates the platform for real without coupling the live app to cloud compute |
| Storage format | Delta Lake for all three layers | ACID writes, schema enforcement, time travel, rerunnable |
| Bronze content | Raw columns, original names, original string values, plus ingest metadata | Lets Silver be rebuilt and DQ be audited against what was received |
| Bronze types | Read every column as string (no `inferSchema`) | Avoids silent coercion; TotalCharges must be seen as received |
| Output database | SQL Server Express via JDBC (mssql-jdbc) | Matches the Power BI source requirement |
| Reconciliation | Exact equality, not tolerance | Same file in, so any difference is a bug |
| Independent check path | Churn counts from the raw CSV using plain Python or pandas | A Spark bug cannot hide itself by being checked with Spark |
| Secrets | Environment variables and a git-ignored `.env`; never in `settings.py` or notebooks | Prevents credential leaks |
| Write mode | Overwrite per layer with explicit schema; rerunning yields identical output | Idempotent pipeline |
| Dropping vs fixing bad rows | Fix and flag, do not drop | Keeps row counts equal across layers |
| Frontend | React + TypeScript (Vite) on Netlify, talking directly to Supabase (Auth, Postgres, Storage) with RLS on every table | No custom backend to secure or host; the browser uses only the anon/publishable key |
| Serving database | Supabase Postgres (auth, storage, RLS; free tier) | One free service covers login, data and file storage |
| Pipeline location | Laptop batch job polling Supabase for uploads with status `queued` | Spark is too heavy for free web hosting; mirrors real batch systems. Uploads wait as `queued` until the pipeline runs, which is expected |
| Secrets | Service-role key only in the laptop git-ignored `.env`; anon key only in the frontend | The service-role key bypasses RLS and must never reach a browser |
| Model | Spark MLlib logistic regression; per-customer reasons from coefficients x standardised feature values; evaluated on a held-out split, AUC reported as measured | Simple and explainable; reasons can be shown to retention staff |
| Warehouse | Star schema in SQL Server (`dim_customer`, `dim_date`, `fact_customer_snapshot`, `fact_retention_action`) | Suits Power BI and T-SQL analysis |

## Evidence and claims

Every claim made about the project (resume, README, app) must:
- come from a real run of the real pipeline or app;
- be recorded automatically in an evidence artifact;
- be reproducible with a stated command;
- label synthetic or scaled data as such.

Numbers are never typed by hand. A generated file `docs/EVIDENCE.md`, built by `python -m src.evidence.collect` from the artifacts, lists for each claim: measured value, source artifact path or query, reproduce command, and date/run_id. Not yet measured values stay `<measured>`.

Evidence types:
- Pipeline run log per stage (rows in/out, seconds, run_id) in a `pipeline_runs` table.
- DQ and reconciliation reports per run.
- pytest JUnit XML and coverage % (pytest-cov).
- Model metrics JSON (AUC, precision/recall on the held-out test set).
- Fault-injection results.
- Scale benchmark results.
- End-to-end app test results (Playwright against the deployed site).
- Databricks Job run history and job definition (exported JSON, account details removed).
- Lighthouse report for the deployed app.
- Security audit report with findings and fixes.

## Phases

Day estimates are rough and assume part-time work.

### Phase 1: Ingestion to Bronze (about 2 days)

Status: done.

Goal: read the CSV and write a Bronze Delta table.

Files: `src/ingestion/spark_session.py`, `src/ingestion/bronze_loader.py`, `src/config/settings.py`.

Acceptance criteria:
- `SparkSession` starts locally with Delta enabled and writes/reads a Delta table on Windows.
- Bronze row count equals the CSV row count (expected 7,043; verify against your own load).
- Bronze column names equal the CSV header exactly, plus `_ingest_ts`, `_source_file`, `_batch_id`.
- All raw columns are string type; no value changed from the file.
- The actual column list is compared with `DATA_SCHEMA.md`; any difference (for example ticket columns) is recorded there.
- Rerunning the stage does not duplicate rows.

### Phase 2: Data quality framework (about 3 days)

Status: done.

Goal: rule-driven checks that run on Bronze and record every result, passes and failures.

Files: `config/dq_rules.json`, `src/quality/rules.py`, `src/quality/runner.py`, `src/quality/report.py`.

Acceptance criteria:
- Rules come from the config file; thresholds are not hardcoded in code.
- The runner records one result row per rule: rule id, column, checked rows, failing rows, failing rate, threshold, status (pass/warn/fail), action. Passing rules are recorded too.
- Results are written to a Delta table `dq_results` and a JSON report.
- The TotalCharges blank rule reports exactly the number of blank rows present (expected 11) and the rule `tenure_range` accepts tenure 0.
- A `fail`-severity rule that trips stops the pipeline before Silver; `warn` and `fix` rules do not.
- Unit tests cover each rule type with both a passing and a failing input, and check the comparison direction of thresholds.

### Phase 3: Silver, Gold and features (about 3 days)

Status: done.

Goal: clean typed Silver and the Gold aggregate tables.

Files: `src/transformations/silver.py`, `gold.py`, `features.py`.

Acceptance criteria:
- Silver has snake_case names and the types in `DATA_SCHEMA.md`.
- `total_charges` is double with no nulls; blank values on tenure-0 rows are set to 0.0 and `total_charges_imputed = true` for exactly those rows.
- Silver row count equals Bronze row count (exclusions expected 0; any exclusion is listed with a reason).
- Derived columns: `tenure_band` (0-12, 13-24, 25-48, 49-72, 73+), `addon_service_count` (number of Yes among online_security, online_backup, device_protection, tech_support, streaming_tv, streaming_movies), `churn_flag` (0/1), `has_internet`.
- Gold tables exist as defined in `DATA_SCHEMA.md`, each with `segment`, `customers`, `churned`, `churn_rate_pct`.
- `churn_rate_pct` is computed from the counts, rounded to 2 decimals, and never divides by zero.

### Phase 4: Reconciliation (about 2 days)

Status: done.

Goal: prove the layers and outputs agree with the source.

Files: `src/reconciliation/source_calc.py`, `checks.py`, `report.py`.

Acceptance criteria:
- All checks in the reconciliation list below are implemented and run in `pipeline.py` after Gold.
- Source values are computed from the raw CSV with plain Python or pandas, not Spark.
- Results go to a Delta table `reconciliation_results` and a JSON report, one row per check with expected, actual, difference and status.
- A deliberately corrupted copy of Silver (in a test) makes at least one check fail.
- Computed numbers are compared with the expected baseline in `DATA_SCHEMA.md`; any mismatch is investigated, not edited away.

### Phase 5: SQL Server star schema, Power BI and analytics (about 5 days)

Goal: a star schema in SQL Server, a Power BI report on it, and documented findings with business value.

Files: `src/output/sqlserver_writer.py`, `sql/ddl/`, `sql/analysis/`, `docs/INSIGHTS.md`, a `.pbix` file kept outside git or small.

Acceptance criteria:
- Tables: `dim_customer` (SCD type 1 is fine), `dim_date`, `fact_customer_snapshot` (one row per customer per snapshot month: tenure, charges, contract, churn flag, risk score added after Phase 6), `fact_retention_action` (populated from Phase 7 data). Column lists are in `DATA_SCHEMA.md`.
- The existing Gold aggregate tables are also loaded.
- DDL is written in `sql/ddl/` and run before the load; tables are not auto-created by Spark. Load is via JDBC from Delta.
- Credentials are read from the environment; the code contains none.
- Reconciliation checks rc09, rc10, rc13 and rc14 are implemented and pass.
- Power BI report on the star schema shows at least: overall churn rate, churn by contract, internet service, payment method and tenure band. Headline numbers match the Gold tables.
- T-SQL analysis queries are in `sql/analysis/`.
- Fault-injection test: `src/evidence/fault_injection.py` injects a known number of defects of each DQ-rule type into a copy of the real CSV (blank ids, duplicates, invalid categories, out-of-range tenure and charges, blank TotalCharges with tenure > 0, broken cross-field rules), runs DQ, and records detected vs injected per rule (detection rate). A report is written; any rule below 100% detection is investigated.
- `docs/INSIGHTS.md` lists findings with business value, for example monthly revenue lost to churn (sum of `MonthlyCharges` of churned customers) and a what-if of moving N% of month-to-month customers to one-year contracts. Every number comes from a query whose SQL is included in the document.

### Phase 6: Churn model (about 4 days)

Status: done.

Goal: an explainable churn model that scores every customer.

Files: `src/model/` (feature pipeline, training, scoring, reasons), `tests/`.

Acceptance criteria:
- Spark MLlib pipeline on Silver features: StringIndexer/OneHotEncoder, StandardScaler, LogisticRegression.
- Stratified train/test split with a fixed seed.
- AUC-ROC and precision/recall at a chosen threshold are reported on the test set (`<measured>`) and written to a JSON report.
- The model is saved.
- All customers are scored: `risk_score`, `risk_band` (high/medium/low, cut-offs documented) and the top 3 reasons per customer.
- No leakage: `churn` and `churn_flag` are excluded from features; `total_charges` is discussed as tenure-correlated.
- Tests cover the feature pipeline and reason generation.

### Phase 7: Supabase integration (about 5 days)

Goal: the pipeline and a hosted database exchange uploads and results securely.

Files: `src/integration/supabase_worker.py`, Supabase migrations kept in `backend/supabase/migrations/`.

Acceptance criteria:
- Postgres tables: `organisations`, `profiles` (user to organisation, role), `uploads` (file path, status queued/processing/done/rejected, timestamps), `pipeline_runs`, `dq_results`, `reconciliation_results`, `customer_scores`, `segment_aggregates`, `retention_actions` (customer, action type, note, outcome, user, timestamp). Key columns are in `DATA_SCHEMA.md`.
- RLS on every table: users read and write only rows of their own organisation; only the service role writes pipeline outputs; users can insert into `uploads` and `retention_actions` only.
- Storage bucket is private, with an upload size limit and a CSV type check.
- `src/integration/supabase_worker.py` polls for `queued` uploads, downloads the file, runs the pipeline, writes results back, and marks the upload `done` or `rejected`. A DQ fail marks it `rejected` with the failed rules recorded.
- The service-role key is read only from the git-ignored `.env`.
- An RLS test shows user A cannot read organisation B's rows.
- Check the Supabase free-tier limits (including pausing after inactivity) at the start of this phase.

Status: done (7a schema, RLS, storage; 7b laptop worker with live end-to-end test).

### Phase 8: React + TypeScript app (about 7 days)

Goal: a public retention workbench app on top of Supabase.

Files: `frontend/` (Vite, Supabase JS client).

Acceptance criteria:
- Pages: sign-in; upload and status; DQ report for an upload; at-risk list (filter by risk band, contract, tenure band; sort by risk); customer detail (risk, reasons, action history, log an action); trends (monthly churn, revenue at risk); pipeline runs (DQ and reconciliation per run).
- The browser uses only the anon/publishable key; no service key anywhere in the bundle.
- Every number comes from the database; none is hardcoded.
- Deployed to Netlify with HTTPS and a public URL.
- End-to-end test with Playwright against the deployed app: sign in, upload the real CSV, (pipeline run on the laptop), upload shows done, DQ report and at-risk list visible, log an action. Results are saved as evidence (finished in Phase 10 if the full flow needs the CI setup).
- Design work uses the design-taste-frontend and impeccable skills.
- Admin panel (role-based user management): migration `0005_admin.sql` and Edge Function `admin-users` (secret key only server-side; server-side validation; allowlisted roles; caller's organisation from the database; CORS allowlist; 50 new users per organisation per 24 hours; audit log without secrets). Roles: platform admin (`private.platform_admins`, CLI-granted only), manager (own organisation), analyst (none).
- Admin UI: `/admin/team` (team table, add employee with one-time temporary password, change role, deactivate/reactivate, reset password, audit log), `/admin/organisations` (platform admin only), forced `/change-password` (min 12 characters) at first login, deactivated users signed out with a message.
- `tests/test_admin.py` proves against the deployed function: manager cannot act across organisations, on self, or with an invalid role; analysts get 403, unauthenticated 401, bad origin rejected; deactivated users cannot sign in and see no rows; users can only clear their own `must_change_password`; audit log visible only to the organisation's managers. Evidence: `data/evidence/admin_panel_test.json`.

### Phase 9: Databricks Free Edition (about 3 days)

Goal: the same `src/` modules run in Databricks as a scheduled Job.

Files: `jobs/run_pipeline.py` (entry script), `databricks.yml` (job definition), exported run history JSON.

Acceptance criteria:
- Check the current Free Edition limits first (compute, storage, external access, scheduling).
- Notebooks run the same `src/` modules; storage paths come from config.
- Delta tables are written to Unity Catalog.
- A scheduled Job runs the pipeline; the job definition is exported into the repo.
- The repo contains the entry script, the job definition and the exported run history of real runs (including a scheduled one). Screenshots are not committed: they show account details, and the exported JSON proves the same runs.
- A README section explains what ran where.
- Scale benchmark on Databricks: the same synthetic datasets as Phase 10, with per-stage seconds and rows/second recorded (`<measured>`).

### Phase 10: Hardening (about 4 days)

Goal: confidence, repeatability and a safe public deployment.

Files: `tests/`, `src/pipeline.py`, `.github/workflows/`, runbook section in `README.md`.

Acceptance criteria:
- Tests are split with a pytest marker `integration`: the quick run takes under 1 minute (`<measured>`); the full run is separate.
- Scale benchmark: `src/evidence/scale_data.py` generates a clearly labelled synthetic dataset by resampling the real rows with new unique ids and small documented noise, at 1M and 10M rows (size limited by laptop disk and RAM; machine specs recorded). The full pipeline runs locally (and on Databricks, Phase 9); per-stage seconds and rows/second go to a benchmark JSON and a table in `docs/EVIDENCE.md`. Claims say "synthetic, resampled from 7,043 real rows".
- The Phase 8 Playwright end-to-end test is run against the deployed app and its results are saved as evidence.
- Test coverage threshold is set and reported (`<measured>`); a CI badge is added to the README.
- `src/evidence/collect.py` generates `docs/EVIDENCE.md`; evidence also includes a Lighthouse report for the deployed app.
- `src/pipeline.py` is the single entry point and writes a per-stage run log.
- GitHub Actions CI runs the quick tests.
- Runbook in `README.md` states how to run, what outputs to expect and how to read the DQ and reconciliation reports.
- Security audit of the repo, the Supabase RLS policies and the deployed site: CSP, HSTS, nosniff and frame-ancestors set at Netlify; no service key and no source maps in the bundle.
- The resume bullets are filled only from `docs/EVIDENCE.md`.

## Data quality rules

Applied to Bronze unless noted. Rules and thresholds live in `config/dq_rules.json`. Each rule has one threshold (`max_failing_rate`, default 0) and one action. Status is `pass` when the failing rate is at or below the threshold; otherwise it is `fail` for action fail, and `warn` for actions warn and fix. "Fix" means Silver corrects the rows and the DQ report records the count.

| Id | Rule | Column | Threshold | Action |
|---|---|---|---|---|
| dq01 | Schema: all expected columns present, no extras | all | exact | fail |
| dq02 | Row count at least 1 | table | >= 1 | fail |
| dq03 | customerID not null | customerID | 0 nulls/blanks | fail |
| dq04 | customerID unique | customerID | 0 rows with a duplicated customerID | fail |
| dq05a | gender in allowed set | gender | 0 invalid | fail |
| dq05b | Yes/No columns in allowed set (Churn only when the file has it) | Partner, Dependents, PhoneService, PaperlessBilling, Churn | 0 invalid | fail |
| dq06a | MultipleLines in allowed set | MultipleLines | 0 invalid | fail |
| dq06b | InternetService in allowed set | InternetService | 0 invalid | fail |
| dq06c | Add-on columns in allowed set | OnlineSecurity, OnlineBackup, DeviceProtection, TechSupport, StreamingTV, StreamingMovies | 0 invalid | fail |
| dq06d | Contract in allowed set | Contract | 0 invalid | fail |
| dq06e | PaymentMethod in allowed set | PaymentMethod | 0 invalid | fail |
| dq07 | SeniorCitizen in {0, 1} | SeniorCitizen | 0 invalid | fail |
| dq08 | tenure is an integer in 0..600 months (0 is valid; the Kaggle file stops at 72) | tenure | 0 outside range | fail |
| dq09 | MonthlyCharges numeric and within 0..10000 (the Kaggle file spans 18.25..118.75) | MonthlyCharges | 0 rows | fail |
| dq10 | TotalCharges blank on a row with tenure = 0 (new, unbilled customer) | TotalCharges | no limit; count recorded (expected 11) | fix |
| dq11 | TotalCharges blank on a row with tenure > 0 | TotalCharges | 0 rows | fail |
| dq11b | TotalCharges non-blank but not numeric | TotalCharges | 0 rows | fail |
| dq12 | Cross-field: InternetService = No implies add-on columns = "No internet service" | add-on columns | 0 violations | warn |
| dq13 | Cross-field: PhoneService = No implies MultipleLines = "No phone service" | MultipleLines | 0 violations | warn |
| dq14 | No null values in any raw column (CSV empty fields become null) | all | 0 rows | fail |

Notes:
- Ranges in dq08 and dq09 come from the public dataset; widen them if your file differs, and record the change.
- If the file has extra columns, add rules for them after reading their values.

## Reconciliation checks

All comparisons are exact. "Source" means computed from the CSV with plain Python or pandas.

| Id | Check |
|---|---|
| rc01 | Source row count = Bronze row count |
| rc02 | Bronze row count = Silver row count + listed exclusions (exclusions expected 0) |
| rc03 | Churned count in Silver = churned count computed from the raw CSV |
| rc04 | Distinct customerID count: source = Bronze = Silver |
| rc05 | For each Gold segment table, sum of `customers` = Silver row count |
| rc06 | For each Gold segment table, sum of `churned` = Silver churned count (expected 1,869) |
| rc07 | Each Gold segment's customers and churned equal the source-computed group counts |
| rc08 | `churn_rate_pct` in Gold = round(100 * churned / customers, 2) for every row |
| rc09 | SQL Server row count = Delta row count, for every loaded table (warehouse, Gold, report tables) |
| rc10 | SQL Server Gold (segment, customers, churned, churn_rate_pct) = Delta Gold exactly, per Gold table |
| rc11 | Number of Silver rows flagged `total_charges_imputed` = DQ dq10 count (expected 11) |
| rc12 | Sum of Silver `total_charges` = source sum of numeric TotalCharges (blanks counted as 0), compared to 2 decimals |
| rc13 | No orphans in SQL Server: every `fact_customer_snapshot` customer_key, service_profile_key and snapshot_date_key exists in its dimension (replaces FOREIGN KEYs) |
| rc14 | `fact_customer_snapshot` for the snapshot month in SQL Server: row count and churned = Silver |

Expected values in `DATA_SCHEMA.md` are a sanity reference only. Checks never use those numbers as their expected side.

## Open questions

- Exact delta-spark version for the installed PySpark: confirm against the Delta compatibility table.
- Databricks Free Edition limits (storage, compute, external access, scheduling) change; check at the start of Phase 9.
- Supabase free-tier limits (including pausing after inactivity): check at Phase 7.
- Whether to simulate later monthly snapshots: the dataset is a single snapshot. Phase 5 may generate a documented synthetic second month for trend demos, clearly labelled synthetic. Decide in Phase 5.
- Whether to add a simple run-log Delta table (stage, start, end, rows) or just log lines. Default: log lines plus a JSON summary.
