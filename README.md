# Churn-Spark

[![CI](https://github.com/Neerajreddy-2005/churn-spark/actions/workflows/ci.yml/badge.svg)](https://github.com/Neerajreddy-2005/churn-spark/actions/workflows/ci.yml)

A retention workbench prototype built on the IBM Telco Customer Churn dataset. A telecom customer-retention team uploads a monthly customer CSV. A PySpark medallion pipeline (Bronze, Silver, Gold in Delta Lake) validates and processes it, and a churn model scores each customer with reasons. A public React + TypeScript app shows the data quality report, an at-risk list, retention action logging, monthly trends and pipeline run history. A SQL Server star schema and Power BI report cover analytics, and a reconciliation step proves the numbers match the source file exactly. Customer churn files in other layouts (bank, SaaS, e-commerce) are accepted too: the system explores the file, checks it, and trains a model on that file's own columns. This is a portfolio prototype, not a product.

Status: pipeline, warehouse, Power BI, churn model, Supabase back end, the live website, "bring your own churn dataset", the scheduled Databricks Job, CI, the scale benchmark and the demo video are done and tested (see [`docs/EVIDENCE.md`](docs/EVIDENCE.md)). Still open: a Playwright end-to-end test against the live site.

Design details are in [docs/SPEC.md](docs/SPEC.md). Column and table definitions are in [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md). The website's design system is in [docs/design/DESIGN.md](docs/design/DESIGN.md).

## Demo

https://github.com/user-attachments/assets/bd569bde-41ea-4a32-b732-5f849071cdcc

## Who it is for and why

**In one line:** each month a subscription company uploads its customer list, and Churn-Spark tells the retention team which customers are most likely to leave, why, and how much revenue is at stake. Every number is checked against the source file.

### The business problem

Subscription businesses (telecom, broadband, pay-TV, streaming) lose recurring revenue every month to customers who cancel. A retention team can only call so many people, so it needs answers to three questions:

1. **Who** is most likely to leave next?
2. **Why**: what about this customer (contract, tenure, services, billing) drives the risk?
3. **Can we trust the numbers?** A wrong file or a silent pipeline bug leads to calling the wrong people and reporting the wrong revenue.

Churn-Spark answers all three in one workflow.

### Who uses it

| Role | What they do in Churn-Spark |
|---|---|
| Retention manager | Uploads the monthly customer export, reads the overview (customers, churn, high-risk count, revenue lost), creates and manages analyst accounts |
| Retention analyst | Works the at-risk list, opens a customer to see the top 3 reasons for the score, logs the action taken and its outcome |
| Data / BI team | Uses the SQL Server star schema and the Power BI report for deeper analysis |
| Platform admin (the operator) | Creates organisations and their managers, protected by MFA; never sees any company's customer data |

Each company (organisation) sees only its own data, enforced in the database by row-level security.

### What a company gets from each upload

| Step | What it gives the business |
|---|---|
| 20 data-quality rules | A broken or wrong file is rejected with a plain reason (for example "duplicate customer IDs, 20 rows") instead of producing wrong numbers |
| Reconciliation (26 checks) | Proof that customer counts, churn counts and charges match the uploaded file exactly at every stage |
| Churn model | A risk score (high / medium / low) for every customer, with the top 3 reasons in plain words |
| Overview and at-risk list | Where to spend the team's limited calling time this month |
| Retention actions and audit log | A record of who was contacted, what was offered and what happened |

### Measured on the public IBM sample (7,043 customers)

- 1,869 customers churned (26.54%), taking 139,130.85 of 456,116.60 monthly revenue (30.50%). No currency is given in the source data. Source: [docs/INSIGHTS.md](docs/INSIGHTS.md).
- The churn model scores AUC 0.8328 on customers it never saw in training.
- With a fixed calling budget of 20% of customers, ranking by model risk reaches 49.33% of the customers who churned. The simple rule "call month-to-month customers" reaches 31.98% with the same budget. Source: `model_metrics.json`.

### What data it accepts

`.csv` or `.xlsx`, up to 5 MB, one row per customer. Every upload goes one of two ways, decided by its columns:

**1. Certified Telco layout** (Kaggle "Telco Customer Churn", the same 20 column names). It runs through the full pipeline: 20 data-quality rules, 26 reconciliation checks and the trained Telco model, with results tracked month by month. The Upload page lists every column and its allowed values.
- **With a `Churn` column (Yes/No)**, for past months whose outcome is known, you also see actual churn and revenue lost.
- **Without a `Churn` column**, for this month's customers whose outcome is not known yet, every customer is still scored, and the overview shows churn as "not known yet" instead of 0.
- A file that is nearly this layout but broken (a missing column, wrong values, semicolons) is rejected with the reason instead of being guessed at.

**2. Any other customer churn file** (bank, SaaS, e-commerce, the 33-column IBM version). Mode 2:
- **Automatic data exploration:** for every column, its type, missing values, distribution and top values. ID and personal columns (like `Surname`) have their values hidden.
- **Suggestions:** the churn column (found by its name, such as Churn, Exited or Attrition, with two values), the customer ID, and columns to leave out, each with a reason: identifiers, personal data, constants, codes like ZIP, dates, and columns that give the answer away.
- **7 general data checks** (`g01`-`g07`).
- **A Spark MLlib model trained on that file alone** (80/20 stratified split, metrics on the holdout), with a leakage guard: a column that predicts the outcome almost perfectly on its own (AUC 0.99 or higher) is removed, and one at 0.90 or higher is flagged.
- **Training:** when the churn column is clear and the checks pass, the model trains straight away; otherwise the file waits for someone to confirm the columns. "Change columns" retrains with different choices.
- **Results:** the same Overview and At-risk pages as the Telco data, through one **Data** picker, including customer pages and retention actions.

A file with no churn column (sales data, for example) gets the exploration only.

### Bring your own churn dataset: measured on public Kaggle files

Each file was run through the real code (`python -m src.evidence.mode2_datasets <files>`). Metrics are on the 20% holdout. Source: `backend/pipeline/data/evidence/mode2_datasets.json`; the files themselves are not committed.

| File | Rows | Churn column found | Left out automatically | Churn rate | Holdout AUC (train) | Riskiest 20% reach | Reconciliation |
|---|---|---|---|---|---|---|---|
| Bank (`Churn_Modelling.csv`) | 10,000 | `Exited` = 1 | RowNumber (identifier), Surname (personal) | 20.37% | 0.770 (0.767) | 48.48% of churners | 16/16 |
| SaaS (`customer_churn_dataset-testing.csv`) | 64,374 | `Churn` = 1 | none | 47.37% | 0.902 (0.905) | 39.63% (best possible 42.22%) | 16/16 |
| E-commerce (`E Commerce Dataset.xlsx`, 2 sheets) | 5,630 | `Churn` = 1 | none; blanks in 7 columns filled and flagged | 16.84% | 0.903 (0.898) | 72.78% | 24/24 |
| IBM 33-column (`Telco_custom_churn.xlsx`) | 7,043 | `Churn Label` = Yes | 3 constants, City, Lat Long, Zip Code, and the leakage columns Churn Value, Churn Score, Churn Reason | 26.54% | 0.868 (0.856) | 57.01% | 28/28 |
| Sales (`sales.csv`) | 100 | none, so exploration only | n/a | n/a | n/a | n/a | n/a |

**Leakage test:** the IBM file was run again with its three outcome columns put back, as if a user ticked them. The guard removed `Churn Value` and `Churn Reason` (single-column AUC 1.000) and flagged `Churn Score` (0.948). With that column still in, the AUC rose from 0.868 to 0.974, which is why the name rule leaves it out by default and the results page warns about it.

### Limits, stated plainly

- **Mode 2 is general but not magic.** It needs one row per customer and a churn column to learn from; anything else gets the exploration only. Its model is retrained per file and is only as good as that file: a small or one-off export gives a rough model, and the holdout AUC is shown so nobody over-trusts it.
- **The certified Telco layout** has the full 20-rule / 26-check pipeline, month-by-month tracking, revenue figures and the SQL Server / Power BI path. Other datasets have the general checks and their own model, but no warehouse or Power BI yet.
- The model is trained on **one public snapshot**. It shows patterns in that data and has not been validated on a real company's history over time.
- The pipeline runs as a **batch job on a laptop** (or Docker). Uploads wait in a queue until the worker processes them, which takes about 1.5 to 2.5 minutes per file.
- It is a **portfolio prototype** that shows data engineering, analytics, BI and ML skills end to end. It is not a commercial product.

## Architecture

```
 LIVE (free tiers)
   Netlify: React + TypeScript app
        ^  |   browser uses anon key only; RLS on every table
        |  v
   Supabase: Auth, Postgres, Storage
        ^  |
        |  |  uploads with status "queued" / results written back
        |  v
 LAPTOP
   PySpark pipeline (polls Supabase, service-role key from .env only)
     CSV -> BRONZE -> DQ checks -> SILVER -> GOLD
         -> model scoring -> reconciliation
         -> results back to Supabase
        |
        v
   SQL Server Express (star schema, JDBC) -> Power BI Desktop

 DATABRICKS Free Edition
   Same src/ modules as a scheduled serverless Job, Unity Catalog Delta tables
```

The pipeline runs on the owner's laptop as a batch job; uploads wait with status `queued` until it runs. The same `src/` modules also run in Databricks Free Edition as a scheduled Job; see `SPEC.md`.

## Proof

Claims about this project are backed by [`docs/EVIDENCE.md`](docs/EVIDENCE.md), generated by `python -m src.evidence.collect` (run from `backend/pipeline/`) from the proof files in `backend/pipeline/data/evidence/`. Rules: every number is measured from a real run, reproducible with the command listed beside it, and never typed by hand. Synthetic or scaled data is always labelled as such. Anything not yet measured shows as "not measured yet".

Scale benchmark (synthetic, resampled from the 7,043 real rows; timings only): the full pipeline including model training and scoring ran on 1M rows in 310.4 s on the laptop and 273.7 s on Databricks serverless, and on 10M rows in 1,136.4 s on the laptop (i5-1235U, 15.7 GB RAM, 6 GB Spark driver); data quality and reconciliation stayed exact (26/26) at every size. Details and per-stage times are in `docs/EVIDENCE.md`.

## Tech stack

| Component | Version / note |
|---|---|
| Python | 3.10 or 3.11 |
| Java | 17 (required by Spark 3.5) |
| PySpark | 3.5.x |
| delta-spark | 3.x matching Spark 3.5 (3.2.x or 3.3.x; check the Delta release table) |
| pytest | current |
| python-dotenv | current |
| SQL Server | Express (local), with SQL Server Management Studio or Azure Data Studio |
| JDBC driver | Microsoft mssql-jdbc, jar for Java 11+ (`jre11` build) |
| Power BI | Desktop (Windows) |
| Spark MLlib | Part of PySpark; logistic regression churn model |
| Supabase | Auth, Postgres, Storage (free tier) |
| React + TypeScript | Vite build, Supabase JS client |
| Netlify | Frontend hosting |
| GitHub Actions | CI running the quick tests |
| Winutils | `winutils.exe` + `hadoop.dll` for Hadoop 3.3.x |

Pin exact versions in `requirements.txt` once Phase 1 installs succeed.

## Folder structure

```
Churn-Spark/
  README.md
  .github/workflows/ci.yml  CI: quick pipeline tests + coverage, frontend lint + build
  backend/
    pipeline/               everything Python / Spark / Docker (run its commands from this folder)
      src/
        pipeline.py         single entry point (python -m src.pipeline), per-stage run log
        config/             settings.py (paths, table names), Supabase and SQL Server settings
        ingestion/          spark_session.py, bronze_loader.py
        quality/            rule definitions, runner, report writer
        transformations/    silver.py, gold.py, features.py
        reconciliation/     independent source calculations, checks, report
        warehouse/          star.py (Delta star schema)
        model/              feature pipeline, training, scoring, reasons
        analysis/           runs sql/analysis and writes docs/INSIGHTS.md
        evidence/           fault injection proof, Mode 2 real-dataset evidence
        output/             SQL Server setup and writer
        generic/            Mode 2: reader (CSV/XLSX), profile, suggestions, general checks, training
        integration/        Supabase worker (dataset_worker.py for Mode 2), admin CLI, migration runner
      jobs/                 Databricks Job entry (run_pipeline.py) and evidence export
      databricks.yml        Databricks Asset Bundle: the scheduled serverless job
      tests/                pytest, local SparkSession fixture
      config/               dq_rules.json (DQ rules and thresholds), no secrets
      sql/ddl/  sql/analysis/   SQL Server tables and T-SQL analysis queries
      Dockerfile  compose.yaml  requirements.txt  requirements-dev.txt  pytest.ini
      .env.example  .env    .env is git-ignored
      data/raw/             Telco-Customer-Churn.csv
      data/evidence/        proof files behind every claim
      data/ (other), models/, jars/   generated locally, git-ignored
    supabase/               migrations/ and functions/admin-users (Edge Function)
  frontend/                 React + TypeScript (Vite) website, netlify.toml
  bi/powerbi/               Churn-Spark.pbix and local test datasets (git-ignored; exports are in docs/powerbi/)
  docs/
    SPEC.md  DATA_SCHEMA.md  INSIGHTS.md  POWERBI_GUIDE.md
    powerbi/                report PDF and page images
    design/                 DESIGN.md, PRODUCT.md, MODE2.md (bring-your-own-dataset design), screenshots/
```

**Where to run commands:** Python, pytest and Docker commands run from `backend/pipeline/` (`cd backend\pipeline`). The virtual environment `.venv` lives in the repository root, so activate it with `..\..\.venv\Scripts\activate` from there. Supabase CLI commands run from `backend/`. Website commands run from `frontend/`.

## Local setup (Windows)

1. **Java 17.** Install a JDK 17 (Temurin or similar). Set `JAVA_HOME` to it and confirm `java -version` prints 17.
2. **Hadoop winutils.** Spark on Windows needs native Hadoop files for local file access.
   - Download `winutils.exe` and `hadoop.dll` for Hadoop 3.3.x from a maintained winutils build (verify the source; use a well-known repository).
   - Place them in `C:\hadoop\bin\`.
   - Set `HADOOP_HOME=C:\hadoop` and add `C:\hadoop\bin` to `PATH`. Open a new terminal afterwards.
3. **Virtual environment.**
   ```
   python -m venv .venv
   .venv\Scripts\activate
   cd backend\pipeline
   pip install "pyspark==3.5.*" "delta-spark==3.3.*" pytest python-dotenv
   ```
   Keep delta-spark on 3.x: delta-spark 4.x requires PySpark 4 and would conflict.
4. **SQL Server Express.**
   - Install SQL Server Express and enable TCP/IP in SQL Server Configuration Manager (static port 1433, or note the port used).
   - Default auth is Windows Authentication (`SQLSERVER_AUTH=integrated`): no password. Put `mssql-jdbc_auth-<version>.x64.dll` (same version as the driver) in a folder on `PATH`, e.g. `C:\hadoop\bin`.
   - For SQL authentication instead, enable mixed mode, create a login with write access to `churn_db` only (not `sa`), and set `SQLSERVER_AUTH=sql`, `SQLSERVER_USER`, `SQLSERVER_PASSWORD`.
   - Create the database and tables once: `python -m src.output.sqlserver_setup` (runs `sql/ddl/`, idempotent).
5. **JDBC drivers and Delta jars.** The first Spark start downloads them from Maven Central (`MSSQL_JDBC_PACKAGE`, `POSTGRES_JDBC_PACKAGE` in `settings.py`). After that, copy them into the git-ignored `backend/pipeline/jars/` folder so later starts never contact the network (the online check sometimes hung for ~10 minutes):
   ```
   mkdir jars
   copy %USERPROFILE%\.ivy2\jars\*.jar jars\
   ```
   `get_spark()` uses `jars/` when all required jars are there and falls back to downloading otherwise.
6. **Dataset.** Download "Telco Customer Churn" from Kaggle and put `Telco-Customer-Churn.csv` in `backend/pipeline/data/raw/`. Check the header row. If the file comes from the PwC internship variant it may have extra columns (for example `numAdminTickets`, `numTechTickets`). If so, update `DATA_SCHEMA.md` and the Bronze/Silver definitions before Phase 1.
7. **Secrets.** In `backend/pipeline/`, copy `.env.example` to `.env` and fill it in.

`.env.example`:

```
SQLSERVER_HOST=localhost
SQLSERVER_PORT=1433
SQLSERVER_DB=churn_db
# integrated = Windows Authentication (default, no password); sql = SQL login (needs the two lines below)
SQLSERVER_AUTH=integrated
# SQLSERVER_USER=churn_etl
# SQLSERVER_PASSWORD=
# Laptop pipeline only. Never put the service-role key in the frontend or in git.
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Databricks credentials go in a separate git-ignored `backend/pipeline/.env.databricks` (two lines, `DATABRICKS_HOST=` and `DATABRICKS_TOKEN=`), so the worker container, which loads `.env`, never receives the token.

`.env` must be listed in `.gitignore` before the first commit. The Databricks token is only used by the CLI on the laptop to deploy and start the job; the job itself needs no secrets.

## Supabase setup

Phase 7a: schema, row-level security and the private upload bucket.

`.env` keys (never commit; git-ignored):

| Key | Used by |
|---|---|
| `SUPABASE_URL` | everything |
| `SUPABASE_ANON_KEY` (publishable, `sb_publishable_...`) | the frontend and the RLS test (acts as a user or anonymous) |
| `SUPABASE_SERVICE_ROLE_KEY` (secret, `sb_secret_...`) | laptop only: admin API and the worker; bypasses RLS, never in a browser |
| `SUPABASE_DB_HOST`, `SUPABASE_DB_PORT`, `SUPABASE_DB_USER`, `SUPABASE_DB_PASSWORD` | JDBC as `postgres` (session pooler): migrations, admin CLI, test seeding |
| `SUPABASE_DB_CA_CERT` (optional) | Path to the project's CA certificate (Supabase dashboard → Database settings → SSL). When set, JDBC uses `sslmode=verify-full` and checks the server; without it, `sslmode=require` (encrypted, not verified). |

```
python -m src.integration.supabase_migrate        # applies supabase/migrations/*.sql in order; re-running does nothing
python -m src.integration.supabase_admin create-org --name "Acme"
python -m src.integration.supabase_admin create-user --email a@acme.test --org-id <uuid> [--role manager]   # prints the password once
python -m src.integration.supabase_admin delete-user --user-id <uuid>
pytest -q tests/test_supabase.py                  # unit + live RLS proof (writes data/evidence/supabase_rls_test.json)
```

Migrations `0001` to `0003` build the schema, security and bucket; `0004_hardening.sql` adds foreign keys on output tables, a limit of 20 uploads per organisation per 24 hours (rows and storage objects), and requires retention actions to target a scored customer.

Sign-ups are off; users exist only through `create-user`. `delete-user` fails for a user who has uploads or retention actions (foreign keys keep the audit trail).

## Run the worker

The laptop worker turns queued Supabase uploads into results. It needs the saved model (`models/churn_lr`) and the `.env` keys above.

```
python -m src.integration.supabase_worker --once            # process every queued upload, then exit
python -m src.integration.supabase_worker --poll 30         # check every 30 seconds until Ctrl+C
python -m src.integration.supabase_worker --once --org-id <uuid> --max 1   # optional filters
```

Upload statuses: `queued` (waiting), `processing` (claimed by a worker), `done` (results are in Supabase; `run_id` is set), `rejected` (the file is unacceptable: wrong columns, failed data quality rules, bad path or over 5 MB; `status_reason` says why and the DQ results are still stored), `failed` (a worker or infrastructure error, or reconciliation mismatch; re-upload). An upload stuck in `processing` for over 60 minutes is marked `failed` ("worker interrupted; please re-upload") the next time the worker starts. Work files go under `data/runs/<run_id>/` (git-ignored) and are deleted when the upload finishes, since the results, DQ and reconciliation rows are in Supabase; one evidence line per upload is appended to `data/evidence/worker_runs.jsonl`.

## Run the worker in Docker (recommended)

The worker (Supabase uploads → pipeline → results) runs in a Linux container, so it needs no Windows Java or winutils setup. Secrets come from `.env` at runtime and are never baked into the image; `data/` and `models/` are mounted from the `backend/pipeline/` folder. Run the `docker compose` commands from `backend/pipeline/`.

```
docker compose up -d --build        # build (first time ~6 min) and start; polls every 60 s
docker compose logs -f worker       # follow the log
docker compose down                 # stop
```

Live end-to-end test inside the container (same 22 checks as on Windows):

```
docker compose run --rm --no-deps -v "${PWD}	ests:/app/tests:ro" -v "${PWD}\pytest.ini:/app/pytest.ini:ro" worker python -m pytest -q tests/test_worker.py
```

The SQL Server and Power BI steps stay on Windows: Windows Authentication does not work from a Linux container.

## Run and test

Run from `backend/pipeline/` with `.venv` activated.

```
python -m src.pipeline                      # full run: bronze -> dq -> silver -> gold -> reconcile -> warehouse -> train -> score -> sqlserver
python -m src.pipeline --no-sqlserver       # same, without the SQL Server copy
python -m src.pipeline --stage bronze       # one stage (bronze, dq, silver, gold, reconcile, warehouse, train, score, sqlserver)
python -m src.integration.supabase_worker   # poll Supabase for queued uploads
python -m src.evidence.collect              # regenerate docs/EVIDENCE.md from data/evidence/
python -m src.evidence.scale_benchmark --rows 1000000 --root <folder outside OneDrive>   # synthetic scale run
pytest -q -m "not integration"              # quick tests (what CI runs)
pytest -q                                   # full run: real data, Delta writes, model training, SQL Server, live Supabase
pip install -r requirements-dev.txt         # adds pytest-cov; then add --cov=src for coverage
```

Every pipeline run, including a failed one, appends one line per run to `data/evidence/pipeline_runs.jsonl` (local history, git-ignored) and rewrites `data/evidence/pipeline_run.json` with per-stage status, seconds and results. A failing stage stops the run and is recorded with its error. Runtime is measured, not targeted.

Measured on the laptop (2026-11-14): full run with SQL Server 189.2 s, all 9 stages ok, SQL Server checks 21/21 pass. Quick tests: 135 passed in 18-27 s on GitHub Actions (ubuntu-latest) and about 1 min 45 s on this Windows laptop (about 28 s of that is Spark start-up and the first job); coverage of `src/` in the quick tier 47.6% on the latest CI run (50.1% before the benchmark and evidence scripts were added; CI fails below 45%). Tests that write Delta tables, train a model, need the real CSV, SQL Server or live Supabase are marked `integration`; the last full run of the pipeline test files passed 130/130.

## Runbook: what a run produces and how to read it

Run `python -m src.pipeline` from `backend/pipeline/`. A good run ends with every stage `ok` and exit code 0. Any `failed` stage stops the run; its error is in `data/evidence/pipeline_run.json` (and in the console).

| Output | Where | Expected on the IBM file |
|---|---|---|
| Bronze / Silver / Gold Delta tables | `data/bronze`, `data/silver`, `data/gold` | 7,043 rows in Bronze and Silver; 5 Gold tables |
| Data quality report | `data/quality/dq_report.json` (+ Delta history `data/quality/dq_results`) | 19 pass, 1 warn, 0 fail |
| Reconciliation report | `data/reconciliation/reconciliation_report.json` (+ Delta history) | 26/26 pass |
| Star schema | `data/warehouse/` | fact 7,043 rows |
| Model and scores | `models/churn_lr`, `data/model/customer_scores` | test AUC-ROC 0.8328; 953 high / 1,665 medium / 4,425 low |
| SQL Server copy + checks | database `churn_db`, `data/reconciliation/sqlserver_report.json` | 12 tables, 21/21 checks pass |
| Run log | `data/evidence/pipeline_run.json` (latest), `pipeline_runs.jsonl` (history) | per-stage status and seconds |

**Reading the data quality report.** `summary` counts rules by status; `results` has one entry per rule with `checked_rows`, `failing_rows`, `failing_rate`, `threshold` and `action`. The rules live in `config/dq_rules.json`.
- `pass`: the failing rate is at or below the threshold.
- `warn`: above the threshold, but the rule's action is `warn` or `fix`, so the run continues. The one expected warning is `dq10` (11 rows): `TotalCharges` is blank only for customers with `tenure = 0`; Silver sets those to 0.0 and flags them `total_charges_imputed`.
- `fail`: above the threshold on a rule with action `fail`. The pipeline stops before Silver, and nothing downstream is overwritten. Look at `rule_id`, `columns` and `failing_rows`, fix the file, and run again.

**Reading the reconciliation report.** Each check compares an `expected` value computed straight from the CSV in plain Python (`src/reconciliation/source_calc.py`, no Spark) with the `actual` value from the Delta tables; `difference` must be 0.
- `rc01`-`rc04`: row counts, churned count and distinct customer ids, source = Bronze = Silver.
- `rc05`-`rc08`: every Gold table adds up to the Silver count and the source churn count, segment by segment, and each `churn_rate_pct` equals `round(100*churned/customers, 2)`.
- `rc11`: imputed rows in Silver = `dq10` failing rows = blank `TotalCharges` in the source.
- `rc12`: the sum of `total_charges` in Silver equals the source sum to the cent.
- Any `fail` stops the run with `Reconciliation failed: <check ids>`. A failure means the transformation changed the numbers; it is a bug to fix, not a threshold to loosen.

**Common problems.** `Column mismatch. Missing: [...]` means the file is not the Telco layout (use bring-your-own dataset on the website instead). A Spark start that hangs for minutes is a Maven lookup: copy the jars into `backend/pipeline/jars/` (see `src/config/settings.py`). A `JAVA_HOME ... has no bin\java` error means `JAVA_HOME` points at the wrong folder.

## Databricks Free Edition

The same `src/` modules run as a scheduled Databricks Job on Free Edition (serverless only, no credit card), writing Delta tables to Unity Catalog. The job is defined in code as a Databricks Asset Bundle (`backend/pipeline/databricks.yml`); the entry script is `backend/pipeline/jobs/run_pipeline.py`.

**What ran where**

| Step | Laptop | Databricks Job |
|---|---|---|
| Bronze, data quality, Silver, Gold, reconciliation | Delta folders under `backend/pipeline/data/` | Unity Catalog tables `workspace.churn_spark.*` (e.g. `silver_telco_customers`) |
| Star schema (dims + fact, MERGE upserts) | Delta folders under `data/warehouse/` | UC tables `warehouse_dim_customer`, `warehouse_fact_customer_snapshot`, ... |
| Train + score (Spark MLlib) | `models/churn_lr` | model and JSON reports in the Volume `/Volumes/workspace/churn_spark/files` |
| SQL Server copy, Power BI, Supabase upload worker, live website | yes | no: Free Edition only reaches trusted domains, not a laptop SQL Server or Supabase |

One switch decides where tables go: `src/config/storage.py`. With `CHURN_UC_SCHEMA` unset (laptop) a stage path is a Delta folder; with `CHURN_UC_SCHEMA=catalog.schema` the same path maps to a managed table. `CHURN_DATA_ROOT` moves the raw CSV, reports and model into the Volume.

**Measured on Databricks (7,043 rows, serverless environment 4, Spark 4.2.0, 2026-11-14):** three successful runs, two started by hand and one by the schedule; job durations including serverless start-up 164.9 s, 139.3 s and 131.9 s (scheduled run: 115.0 s inside the pipeline itself). One earlier run failed and is kept in the history (the `__file__` issue below). Results matched the laptop exactly: data quality 19 pass / 1 warn / 0 fail, reconciliation 26/26 pass, test AUC-ROC 0.8328 and AUC-PR 0.6624, risk bands 953 high / 1,665 medium / 4,425 low. Proof: `data/evidence/databricks_run.json` (per-stage seconds), `databricks_run_history.json` and `databricks_job_definition.json` (both exported with `python jobs/export_evidence.py`; emails and workspace URLs removed).

**Serverless differences we found (by a probe run, not assumed):** `df.cache()` and `spark.sparkContext` are not supported, so `storage.cache()` is a no-op there; Spark ML needs serverless environment version 4 (it fails on 3); the script runs through `exec()`, so `__file__` is undefined and the bundle passes the code folder as `--code-root`.

**Set up and run (from `backend/pipeline/`, with `DATABRICKS_HOST` and `DATABRICKS_TOKEN` in `.env.databricks` (load them into the shell first) and the [Databricks CLI](https://github.com/databricks/cli) installed):**

```
databricks schemas create churn_spark workspace
databricks volumes create workspace churn_spark files MANAGED
databricks fs mkdir dbfs:/Volumes/workspace/churn_spark/files/raw
databricks fs cp data/raw/Telco-Customer-Churn.csv dbfs:/Volumes/workspace/churn_spark/files/raw/
databricks bundle deploy          # creates/updates the job (daily 06:00 Asia/Kolkata)
databricks bundle run churn_pipeline
python jobs/export_evidence.py    # job definition + run history into data/evidence/
```

In Git Bash, set `MSYS_NO_PATHCONV=1` first, or it rewrites `/Volumes/...` and `/Users/...` into Windows paths.

## Resume bullets

Every number below is in [`docs/EVIDENCE.md`](docs/EVIDENCE.md) with its proof file and the command that reproduces it. Synthetic numbers are labelled.

- Built a PySpark and Delta Lake medallion pipeline (Bronze/Silver/Gold) on 7,043 customer records with 20 automated data-quality rules and 26 exact reconciliation checks (26/26 pass) against an independent pure-Python recount; fault injection planted 425 defects and all 450 expected failing rows were caught, and a bad file is blocked before Silver.
- Designed a SQL Server star schema (fact 7,043 rows, 3 dimensions; 21/21 cross-checks against Delta) and a Power BI report; SQL analysis showed 139,130.85 of 456,116.60 monthly revenue (30.50%) came from customers who churned.
- Trained an explainable Spark MLlib logistic regression churn model (held-out AUC-ROC 0.8328) with top-3 reasons per customer and no label leakage; its top 20% riskiest customers capture 49.33% of churners (lift 2.47).
- Built and deployed a retention workbench (React + TypeScript on Netlify, Supabase with row-level security): 63/63 live RLS checks and 130/130 admin-panel checks pass; Lighthouse 100 accessibility and 100 SEO; CSP, HSTS and clickjacking headers verified on the live site.
- Ran the same modules as a scheduled Databricks serverless Job writing Unity Catalog tables (results identical to the laptop), and benchmarked the full pipeline on synthetic data resampled from the real rows: 10M rows in 1,136 s on a laptop, 1M rows in 274 s on Databricks; 184 pytest tests, 135 run in CI in under 30 s.

## Interview notes

How to talk about this project, and the real problems hit while building it. Every number here comes from a file in `backend/pipeline/data/evidence/` or a recorded test run; the right-hand column says which.

### The 30-second pitch

"I built a churn-retention workbench end to end. Customer data goes through a PySpark and Delta Lake pipeline with 20 data-quality rules and 26 exact reconciliation checks, then a Spark MLlib model scores every customer and explains why. A React website on Supabase lets a retention team see who is likely to leave, open a customer and log what they did, with row-level security keeping each company's data separate. I proved each part with automated tests whose results are saved as evidence files."

### Numbers you can quote (and where they come from)

| Claim | Number | Source |
|---|---|---|
| Rows processed per upload | 7,043 customers | `dq_report.json`, `reconciliation_report.json` |
| Data-quality rules | 20 rules: 19 pass, 1 warn (11 blank `TotalCharges` at tenure 0) | `dq_report.json` |
| Reconciliation | 26 exact checks, all pass, against an independent Python recount | `reconciliation_report.json` |
| Fault injection | 425 defects planted across 17 rules; all 450 expected failing rows detected (some defects trip two rules); every rule exact; the bad file is blocked | `fault_injection.json` (`overall`) |
| Churn model | Test AUC 0.8328 (train 0.8489), no label leakage | `model_metrics.json` |
| Model vs simple rule | Calling the top 20% by risk reaches 49.33% of churners; the "month-to-month" rule reaches 31.98% at the same budget | `model_metrics.json` (`capture.top_20`, `baseline.rule_capture_at_budget_pct`) |
| Business finding | Monthly revenue lost 139,130.85 of 456,116.60 (30.50%) | `analysis/02_revenue_at_risk.json` |
| Riskiest segment | Month-to-month + fiber + electronic check + no tech support + no security: 651 of 989 churned (65.82%); 651 of all 1,869 churners = 34.8% | `analysis/07_high_risk_profiles.json`, `analysis/01_churn_overview.json` |
| Row-level security | 63 of 63 live checks pass (one company can never see another's data) | `supabase_rls_test.json` |
| Admin and accounts | 130 of 130 live checks pass (roles, 2FA, forced password change, audit log) | `admin_panel_test.json` |
| Worker end to end | 22 of 22 live checks pass (real CSV upload to results in Supabase) | `worker_e2e_test.json` |
| Bring your own dataset | 4 public churn files trained on their own columns: holdout AUC 0.770 (bank), 0.902 (SaaS), 0.903 (e-commerce), 0.868 (IBM 33-column); reconciliation all pass; a non-churn file gets exploration only | `mode2_datasets.json` |
| Leakage guard | Outcome columns put back are caught: 2 removed at single-column AUC 1.000, 1 flagged at 0.948 | `mode2_datasets.json` (`leakage columns put back`) |
| Mode 2 live | 39 of 39 live checks pass (auto-train, change columns, other org blocked, actions only on own scored rows, Excel, broken files) | `datasets_live_test.json` |
| Automated tests | 184 pytest tests: 135 quick ones run in CI in 18-27 s (coverage 47.6%), 46 integration/live | `pytest`, GitHub Actions log |
| Databricks | Same modules as a scheduled serverless Job; results identical to the laptop (AUC 0.8328, 26/26 reconciliation) | `databricks_run.json`, `databricks_run_history.json` |
| Scale (synthetic, resampled from 7,043 real rows) | 1M rows: 310.4 s laptop, 273.7 s Databricks; 10M rows: 1,136.4 s laptop; reconciliation exact at every size | `scale_benchmark.json` |
| Live site | Lighthouse performance 95-100, accessibility 100, SEO 100; security headers verified | `lighthouse.json`, `security_check.json` |

### Problems we hit and how we solved them

Use the pattern **problem → cause → fix → result** when you tell these. Interviewers care most about how you found the cause.

**Bring-your-own-dataset problems (short versions):**
- *Excel uploads would have been corrupted:* the worker downloaded every file as UTF-8 text. That is fine for CSV but breaks a binary `.xlsx`. Fixed with a raw-bytes download, and a live test uploads a real workbook.
- *A leak with an innocent name:* the name rule catches "Churn Score", but not a column called "Account Status" that copies the outcome. A single-column AUC check catches it by behaviour, and a test proves it.
- *A fake spike in the histograms:* Spark's `least()` skips NULLs, so `least(null, 9)` put every blank value in the last bin. A unit test caught it; blanks are now excluded explicitly.
- *A security check that could pass by accident:* in SQL, `jsonb_typeof(missing_key) <> 'array'` is NULL, not true, so a review with a missing field would slip through. The check uses `IS DISTINCT FROM`, and a live test sends a review with the field missing.

**1. Spark took about 10 minutes to start.**
- *Problem:* every Spark session sat silent for about 626 seconds before doing anything.
- *Cause:* Spark was resolving the Delta and JDBC jars from Maven Central over the network on every start, and the lookup sometimes hung.
- *Fix:* copied the jars into a local `jars/` folder and pointed `spark.jars` at them. A plain `C:/...` path failed with "No FileSystem for scheme C", so the paths are given as `file://` URIs. Docker still downloads them once at image build time.
- *Result:* the live test suites dropped to about 70 seconds.
- *Say it as:* "I profiled where the time went, found it was dependency resolution rather than our code, and made startup work offline."

**2. PySpark hung forever with no error.**
- *Cause:* an old `JAVA_HOME` still pointed at Java 8, so `spark-submit` exited silently and PySpark waited for it.
- *Fix:* `get_spark()` now checks `JAVA_HOME` first and fails fast with a clear message.
- *Say it as:* "A silent hang is the worst kind of failure, so I turned it into an immediate, readable error."

**3. 37 tests failed at once, then passed when re-run.**
- *Cause:* two Spark test runs were running at the same time and fought over the same local Delta folders.
- *Fix:* run one Spark process at a time; tests write to their own temporary folders and a separate test database (`churn_db_test`).
- *Say it as:* "The failures were in the test setup, not the code. I isolated test state so results became repeatable."

**4. A data-quality rule passed when it should have failed.**
- *Cause:* the rule compared a rounded failure rate against its threshold, so a tiny real failure rounded down to zero.
- *Fix:* compare the exact rate; round only for display. Also switched a plain `cast` on tenure to `try_cast`, so one bad value can't crash the whole job.
- *Say it as:* "I made sure the check measures what it claims to measure, then proved it with fault injection: 425 planted defects, and every rule flagged exactly the rows it should (450 of 450)."

**5. The admin function returned HTTP 500 in production.**
- *Cause:* Supabase's "auto-expose new tables" was off (a deliberate security choice), so the server-side role had no table privileges at all.
- *Fix:* migration `0007` grants exactly the privileges the function needs, nothing more.
- *Say it as:* "Secure defaults broke a feature. I fixed it with the least privilege that works instead of turning the default off."

**6. "Change your password on first login" could be skipped.**
- *Cause:* the first version only enforced it in the website, so a user could call the API directly.
- *Fix:* enforced in the database (migration `0006`): until the password is changed the user can read no company data, and users can't clear the flag themselves.
- *Say it as:* "Anything enforced only in the browser isn't enforced. I moved the rule to the server and tested it live."

**7. Separation of duties for the platform admin.**
- *Design:* the platform admin account belongs to no company, sees no customer data and must use two-factor sign-in (TOTP). The database and the Edge Function both check for it, and triggers stop one account from being both admin and company member.
- *Say it as:* "The person who manages accounts can't read the business data. That limits the damage if that account is ever compromised."

**8. The admin's password change silently did nothing.**
- *Cause:* checking the current password signs in again, which drops the session's 2FA level. The page that guards admin screens reacted by replacing the password page with its own code prompt, which threw away the new password before it was sent.
- *Fix:* the password page holds that guard while it runs its own code step, and releases it afterwards so the guard still re-checks.
- *Say it as:* "I reproduced it, traced it to two security features interacting, and fixed the interaction without weakening either."

**9. "Undo" without a delete permission.**
- *Constraint:* retention actions are an audit trail, so the database allows no DELETE on them.
- *Fix:* the website doesn't write the action until a 5-second Undo window has passed. Undo means nothing was written, so no delete is ever needed.
- *Say it as:* "I kept the audit rule intact and still gave users an undo."

**10. Content Security Policy blocked the fonts.**
- *Cause:* the build tool inlined small font files as `data:` URIs, which the strict policy blocked.
- *Fix:* turned off asset inlining instead of loosening the policy, then checked the production build under the real headers: 0 violations.
- *Say it as:* "When security and convenience clashed, I changed the build, not the policy."

**11. Docker worker and Windows-only parts.**
- *Fact:* the worker runs in a Linux container with Java 17 and the jars built in, so it needs no Windows setup. A real upload takes about 178 seconds in Docker versus about 120 on Windows, because of slower file access through the mounted folder; that is noted for later optimisation.
- *Limit:* the SQL Server and Power BI steps stay on Windows, because SQL Server's Windows sign-in doesn't work from a Linux container.
- *Say it as:* "I containerised what made sense and documented the trade-off with measured numbers."

**12. A credential was exposed once.**
- *What happened:* a database connection string was accidentally pasted into a chat during setup.
- *Response:* treated it as an incident. Rotated the password immediately; since then, setup checks only print whether a secret is present, never its value. A secret scan runs before every push to GitHub.
- *Say it as:* "Mistakes happen; what matters is rotating immediately and changing the process so it can't recur."

### Questions you will probably get

- **"Why Spark for 7,043 rows?"** The data is small on purpose, so it runs on a laptop. The point is the method: the same modules run unchanged as a Databricks Job. Be honest that the dataset is small; the engineering (validation, reconciliation, tests, security) is what scales.
- **"How do you know the numbers are right?"** Three ways: data-quality rules on input, 26 exact reconciliation checks against an independent recount, and fault injection proving the checks catch planted errors.
- **"Why logistic regression and not XGBoost?"** It's explainable per customer (the top 3 reasons shown in the app), and it already beats the simple business rule clearly (49.33% vs 31.98% of churners reached with the same effort).
- **"How is one company's data kept from another?"** Row-level security in Postgres, scoped by the user's company, with 63 live tests that try to cross the boundary and must fail.
- **"Does it work on my data?"** "If it is customer-level churn data, yes: it profiles the file, checks it and trains a model on your own columns, with the holdout AUC shown. I tested it on four public churn datasets from banking, SaaS, e-commerce and telecom. If it is not churn data, you get the data exploration and a clear message."
- **"What would you do next?"** Databricks Job with Unity Catalog, CI on GitHub Actions, a faster Docker volume for the worker, and monitoring for model drift.

### What not to claim

- Don't call it a product with real customers. It's a prototype that proves skills.
- Don't quote a number that isn't in an evidence file. If asked about something not measured, say so.
- The dataset is the public IBM Telco Customer Churn dataset. The snapshot month (2026-10 / 2026-11) is assigned by the pipeline, because the source has no dates.

## Admin panel

**Separation of duties:** a **platform admin** is an account with NO organisation (no `profiles` row). It creates organisations and manages accounts across all organisations, but sees no business data (scores, uploads, runs, retention actions stay org-scoped). An account is never both a platform admin and an organisation member; database triggers (migration `0008_admin_separation.sql`) enforce this. Platform admins must also use TOTP two-factor sign-in (enforced in the database policies and the Edge Function, not just the browser).

Roles: **platform admin** (the owner's admin account; stored in `private.platform_admins`, which the Data API cannot reach), **manager** (in their own organisation only: adds analysts, deactivates/reactivates analysts and renames analysts or themselves; cannot create or change managers, change roles or reset passwords; cannot act on themselves except renaming; reads their organisation's audit log) and **analyst** (uses the app, no admin access).

All user management goes through the Edge Function `backend/supabase/functions/admin-users`. It runs with the secret key (auto-provided by Supabase, never in the browser), checks the caller's JWT, takes the caller's organisation from the database, validates every input, rate-limits to 50 new users per organisation per 24 hours and writes `audit_log`. CORS allows only the origins in the `ALLOWED_ORIGINS` secret.

### Owner cheat-sheet (run in `backend/pipeline/` with `.venv` activated)

The platform admin is a **separate account** with its own email, never the same login as an organisation manager. In the app it shows only **Organisations** (list, create, open one to add members, change roles, deactivate/reactivate, break-glass password reset) and **Audit log** (all organisations).

| Task | Command / where |
|---|---|
| Create an organisation (CLI alternative to the Organisations page) | `python -m src.integration.supabase_admin create-org --name "Company name"` → prints the organisation ID |
| Create a user (CLI alternative to the Team page) | `python -m src.integration.supabase_admin create-user --email <email> --org-id <id> --role manager` → prints the password **once**; save it, never paste it in chat |
| Create the platform admin account | `python -m src.integration.supabase_admin create-platform-admin --email <new email>` → prints the password **once**; creates an account with no organisation |
| Make an existing account platform admin | `python -m src.integration.supabase_admin make-platform-admin --email <email>` (refused if the account belongs to an organisation) |
| Undo a wrong grant | `python -m src.integration.supabase_admin revoke-platform-admin --email <email>` |
| See who is platform admin | Supabase dashboard → SQL editor: `select u.email from private.platform_admins a join auth.users u on u.id = a.user_id;` |

Notes:
- `make-platform-admin` only works for an account that already exists and has no organisation; otherwise it refuses (use `create-platform-admin` with a separate email).
- **One-time migration of the owner:** (1) apply migration 0008 and deploy the function; (2) `create-platform-admin --email <separate admin email>`; (3) sign in as it, set up two-factor (below); (4) only then `revoke-platform-admin --email <old manager account>`. Until step 4 the old account is in both tables (the triggers only check new rows); the function already treats it as an ordinary manager.
- **Admin two-factor (TOTP):** at the first admin sign-in the app shows a QR code; scan it with an authenticator app (Google Authenticator, Authy, 1Password, ...), enter the 6-digit code. Every later admin sign-in asks for a code. Without a verified code the admin sees nothing (RLS needs `aal2`) and the function answers 403 `mfa_required`. Recovery if the phone is lost: run `revoke-platform-admin` then `delete-user` for the admin, and `create-platform-admin` again.
- These commands need the secret `.env` on this laptop, so nobody can run them from the website.
- **Who may do what:** managers create analysts only; manager accounts and role changes are platform-admin only (the function answers 403 `forbidden_role` / `forbidden`). Password resets are self-service: users use "Forgot password?" by email. The platform admin keeps a break-glass "Reset password" button (issues a temporary password and forces a change at next sign-in); managers cannot reset passwords.
- Organisation members see Team (managers only) and the normal pages; an admin account sees only Organisations and Audit log.

### Deploying the admin function

```
# log in with a project-scoped access token (Supabase → Account → Access Tokens; scope: churn-spark only;
# permissions: Project Settings read, Edge Functions read-write, Edge Function Secrets read-write; short expiry)
cd backend                      # the Supabase CLI looks for supabase/ in the current folder
npx supabase logout
npx supabase login --token <token>
npx supabase@3.1.0 functions list --remote=mknvrbjrbmuqyoahokgu    # check the CLI sees churn-spark

npx supabase@3.1.0 functions deploy admin-users --remote=mknvrbjrbmuqyoahokgu --use-api
npx supabase@3.1.0 secrets set ALLOWED_ORIGINS=http://localhost:5173 --remote=mknvrbjrbmuqyoahokgu   # comma-separate more origins (the deployed site)
pytest -q tests/test_admin.py     # live proof (writes data/evidence/admin_panel_test.json)
```

The CLI asks for confirmation before changing the production project; answer `y`. The deployed function keeps running after the token expires; a new token is only needed to deploy again.

Managers open `/admin/team`, fill "Add employee" (name, email; always an analyst) and read out the temporary password, which is shown once and never stored. At first sign-in the employee is forced to `/change-password` (min 12 characters) before anything else. Platform admins use `/admin/organisations` to create an organisation plus its first manager, and `/admin/organisations/<id>` to manage that organisation's members. A deactivated user cannot sign in, and an existing session sees no data.

**Edit name:** managers (own organisation) can rename analysts and themselves, but not other managers; platform admins (named organisation) can rename any member, with the inline "Edit name" button on the Team / organisation pages (renaming self is the only self-action a manager may do). Function action `set_name` (`user_id`, `display_name` 1-100 characters), audited as "changed name". Migration `0009` also copies `auth.users.email` into profiles that have no email (older accounts).

**Audit log:** the function writes `actor_label` and `target_label` (display name or email; a platform admin without a profile reads "Platform admin (<email>)") into each entry's `details`, never a password. The Team and Audit pages show When, (Organisation), Action in words, By, Target and readable Details. Migration `0009` backfills the labels for older entries.

### Forgot password (emailed one-time link)

Login has "Forgot password?" -> `/forgot-password` (email, 60 s client cooldown). It calls Supabase `resetPasswordForEmail` and always shows the same message ("If an account exists for that email, a reset link has been sent. It expires in 1 hour."), so nobody can learn which emails have accounts. The link opens `/reset-password` with a recovery session; the user sets a new password (min 12, show/hide toggle). If the account has a verified TOTP factor, Supabase requires an `aal2` session for the password change, so the page then asks for the 6-digit code and retries. On success every session is signed out (`signOut({ scope: 'global' })`) and the user signs in again. An invalid or expired link shows a message and a link back to `/forgot-password`. A reset also satisfies a pending forced change (the 0006 trigger clears `must_change_password` on any real password change).

Prerequisites, run by the project owner in the Supabase dashboard (not done by the code):
1. **Custom SMTP** (Authentication -> SMTP settings). The built-in mail service only sends to team members and is heavily rate limited, so real users will not receive reset emails without it.
2. **URL configuration** (Authentication -> URL Configuration): set the Site URL, and add Redirect URLs `http://localhost:5173/reset-password` and, once deployed, `https://<netlify-site>/reset-password`. Links to a URL not on the list are rejected.


### Changing or recovering a password

| Situation | What to do |
|---|---|
| Change (know current password) | Account menu (header, your email) -> "Change password" (`/account/password`). Needs the current password, then a new one (min 12, different). Platform admins also enter their 2FA code. Other devices are signed out; this one stays signed in. |
| Forgot | "Forgot password?" on the login page -> emailed link. Platform admins also enter their 2FA code. All sessions are signed out afterwards. |
| No access to the email | A platform admin uses the break-glass "Reset password" button on the organisation page (temporary password, forced change at next sign-in). |
| Compromised account | A platform admin deactivates the account (organisation page); reset the password before reactivating. |

## Frontend (demo)

A plain functional React + TypeScript (Vite) demo in `frontend/`. It uses only the Supabase publishable key; all data goes through RLS as the signed-in user. Design work comes later.

```
cd frontend
npm ci
cp .env.example .env.local     # then fill VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY (publishable key only)
npm run dev                    # http://localhost:5173
npm run build                  # type-check and production build
```

Pages: `/login`, `/` Overview (segments, risk bands), `/upload` (CSV upload and status), `/customers` (at-risk list) and `/customers/:id` (detail, log actions), `/runs` and `/runs/:runId` (DQ and reconciliation). Sign-ups are disabled; users are created in Supabase. The header account menu offers "Change password" (`/account/password`) and "Sign out"; see "Changing or recovering a password" above.
