# Data Schema

Source: IBM Telco Customer Churn (Kaggle "Telco Customer Churn"), file `Telco-Customer-Churn.csv`. Expected 7,043 rows and 21 columns, one row per customer.

The PwC internship version of this dataset may contain extra columns (for example `numAdminTickets`, `numTechTickets`). After downloading, check the header. If the column list differs from the table below, update this file (raw table, Silver mapping, SQL DDL) before starting Phase 1.

There is no age column, no billing-cycle column and no call-centre metrics in this dataset. `SeniorCitizen` is the only age-related field.

## Raw columns

Type is what the CSV contains. In Bronze every column is read as string; Silver applies the target type.

| # | Column | CSV type | Allowed values / range | Notes |
|---|---|---|---|---|
| 1 | customerID | string | unique id, e.g. `7590-VHVEG` | primary key |
| 2 | gender | string | Male, Female | |
| 3 | SeniorCitizen | int | 0, 1 | only numeric flag; not Yes/No |
| 4 | Partner | string | Yes, No | |
| 5 | Dependents | string | Yes, No | |
| 6 | tenure | int | 0..72 months | 0 = new customer |
| 7 | PhoneService | string | Yes, No | |
| 8 | MultipleLines | string | Yes, No, No phone service | |
| 9 | InternetService | string | DSL, Fiber optic, No | never the value "Yes" |
| 10 | OnlineSecurity | string | Yes, No, No internet service | |
| 11 | OnlineBackup | string | Yes, No, No internet service | |
| 12 | DeviceProtection | string | Yes, No, No internet service | |
| 13 | TechSupport | string | Yes, No, No internet service | |
| 14 | StreamingTV | string | Yes, No, No internet service | |
| 15 | StreamingMovies | string | Yes, No, No internet service | |
| 16 | Contract | string | Month-to-month, One year, Two year | |
| 17 | PaperlessBilling | string | Yes, No | |
| 18 | PaymentMethod | string | Electronic check, Mailed check, Bank transfer (automatic), Credit card (automatic) | |
| 19 | MonthlyCharges | double | about 18.25..118.75 | |
| 20 | TotalCharges | **string** | numeric text, or blank `" "` | blank for 11 rows, all tenure = 0 |
| 21 | Churn | string | Yes, No | target label |

### Known data issue: blank TotalCharges

- `TotalCharges` is read as a string because 11 rows contain a single space instead of a number.
- All 11 rows have `tenure = 0`: customers who have not yet been billed.
- DQ rule dq10 detects and counts them (expected 11; verify on your own load). Rule dq11 fails if a blank appears on any row with tenure > 0. The `tenure` range rule must accept 0.
- Silver handling: trim, cast to double, and set `total_charges = 0.0` where the value is blank and `tenure = 0`. Reason: no bills have been issued yet, so zero is the accurate total, and keeping the row preserves counts. Rows are flagged with `total_charges_imputed = true`.

Illustrative Silver fix (PySpark):

```python
from pyspark.sql import functions as F

blank_new = (F.trim("TotalCharges") == "") & F.coalesce(
    F.expr("try_cast(trim(tenure) AS INT)") == 0, F.lit(False)
)
silver = bronze.withColumn(
    "total_charges",
    F.when(blank_new, F.lit(0.0))
     .otherwise(F.expr("try_cast(trim(TotalCharges) AS DOUBLE)")),
).withColumn("total_charges_imputed", blank_new)
```

`try_cast` is used because Databricks serverless runs with ANSI mode on, where a plain `cast` of `""` raises an error instead of returning null. A blank on any other row yields null, and DQ dq11 should already have failed the run.

## Layers

### Bronze

- Delta table `bronze_telco_customers`.
- The 21 raw columns with original names, all string, values unchanged.
- Added metadata: `_ingest_ts` (timestamp), `_source_file` (string), `_batch_id` (string).
- Row count must equal the CSV row count.

### Silver

- Delta table `silver_telco_customers`.
- snake_case names, typed (see mapping), cleaned, plus derived columns.
- Row count equals Bronze (no rows dropped in the expected case).
- Yes/No columns are converted to boolean; three-state columns (MultipleLines, add-ons) stay as strings to preserve "No internet service" / "No phone service".

Derived columns:

| Column | Type | Definition |
|---|---|---|
| total_charges_imputed | boolean | true when TotalCharges was blank and set to 0.0 |
| churn_flag | int | 1 if Churn = Yes else 0 |
| has_internet | boolean | internet_service <> 'No' |
| tenure_band | string | 0-12, 13-24, 25-48, 49-72 months (tenure 0 falls in 0-12) |
| addon_service_count | int | count of Yes among online_security, online_backup, device_protection, tech_support, streaming_tv, streaming_movies (0..6) |

### Gold

- Delta tables, one per segment. Common columns: `segment` (string), `customers` (long), `churned` (long), `churn_rate_pct` (double, 2 decimals) = 100 * churned / customers.

| Table | Segment values |
|---|---|
| gold_churn_overall | single row, segment = `All` |
| gold_churn_by_internet_service | DSL, Fiber optic, No |
| gold_churn_by_contract | Month-to-month, One year, Two year |
| gold_churn_by_payment_method | 4 payment methods |
| gold_churn_by_tenure_band | 0-12, 13-24, 25-48, 49-72 |

Further segment tables (senior citizen, add-on count, paperless billing) can be added later using the same four columns; they are not required.

## Silver rename mapping

| Bronze (raw) | Silver | Silver type |
|---|---|---|
| customerID | customer_id | string |
| gender | gender | string |
| SeniorCitizen | senior_citizen | boolean (from 0/1) |
| Partner | partner | boolean |
| Dependents | dependents | boolean |
| tenure | tenure_months | int |
| PhoneService | phone_service | boolean |
| MultipleLines | multiple_lines | string |
| InternetService | internet_service | string |
| OnlineSecurity | online_security | string |
| OnlineBackup | online_backup | string |
| DeviceProtection | device_protection | string |
| TechSupport | tech_support | string |
| StreamingTV | streaming_tv | string |
| StreamingMovies | streaming_movies | string |
| Contract | contract | string |
| PaperlessBilling | paperless_billing | boolean |
| PaymentMethod | payment_method | string |
| MonthlyCharges | monthly_charges | double |
| TotalCharges | total_charges | double (fixed as above) |
| Churn | churn | boolean (plus `churn_flag`) |

Bronze metadata columns are carried into Silver unchanged.

## SQL Server target schema

Database `churn_db`, schema `churn`. The DDL lives in `sql/ddl/` (run in order by `python -m src.output.sqlserver_setup`; idempotent, statements separated by `GO` lines). Spark writes into the existing tables with truncate and insert, so the DDL types are kept.

| File | Contents |
|---|---|
| `00_create_database.sql` | `churn_db` (runs against master) |
| `01_schema.sql` | schema `churn` |
| `02_warehouse.sql` | dim_customer, dim_service_profile, dim_date, fact_customer_snapshot, fact_retention_action |
| `03_gold.sql` | the five Gold tables (`segment`, `customers`, `churned`, `churn_rate_pct` DECIMAL(5,2)) |
| `04_reports.sql` | dq_results, reconciliation_results |
| `05_model.sql` | customer_scores |

There are no FOREIGN KEY constraints because every reload truncates the tables; integrity is proven by reconciliation check rc13 instead. Primary keys exist on all tables. Power BI should read only the `churn` schema, using a read-only login.

## Warehouse (star schema)

Delta tables in `data/warehouse/<table>` are the source of truth, built by `python -m src.warehouse.star` from Silver. SQL Server (`churn` schema) is a serving copy reloaded from Delta by `python -m src.output.sqlserver_writer`. Types below are the SQL Server types.

**Snapshot month.** The source CSV has no date, so the month is assigned: `SNAPSHOT_MONTH = "2026-10"` in `settings.py` (override with `--snapshot-month yyyy-mm`). `snapshot_date_key` is the first day of that month.

### dim_customer

Stable attributes only. SCD type 1: attributes overwritten, `last_seen_date_key` moved forward. Built with a Delta MERGE on `customer_id`; keys never change once assigned, new customers get max key + row_number (ordered by customer_id).

| Column | Type | Key | Notes |
|---|---|---|---|
| customer_key | BIGINT | PK | surrogate |
| customer_id | NVARCHAR(20) | unique | business key |
| gender | NVARCHAR(10) | | |
| senior_citizen | BIT | | |
| partner | BIT | | |
| dependents | BIT | | |
| first_seen_date_key | INT | | snapshot month the customer first appeared |
| last_seen_date_key | INT | | latest snapshot month |

### dim_service_profile

Junk dimension: one row per distinct combination of service and billing attributes, inserted if absent (MERGE).

`service_profile_key` BIGINT PK = `xxhash64(concat_ws('|', ...))` over these columns cast to string, in this order: contract, internet_service, phone_service, multiple_lines, online_security, online_backup, device_protection, tech_support, streaming_tv, streaming_movies, paperless_billing, payment_method. Those twelve columns are also stored (phone_service and paperless_billing as BIT, the rest NVARCHAR).

### dim_date

Generated calendar 2020-01-01..2030-12-31, overwritten each build.

| Column | Type | Notes |
|---|---|---|
| date_key | INT PK | yyyymmdd |
| full_date | DATE unique | |
| year / quarter / month | SMALLINT / TINYINT / TINYINT | |
| month_name | NVARCHAR(10) | |
| year_month | CHAR(7) | yyyy-mm |
| is_month_start | BIT | |

### fact_customer_snapshot

Grain: one row per customer per snapshot month. Delta is partitioned by `snapshot_date_key` and a build overwrites only its own month (`replaceWhere`), so re-running a month is idempotent and other months are untouched. SQL Server: clustered PK (snapshot_date_key, customer_key).

| Column | Type | Notes |
|---|---|---|
| snapshot_date_key | INT | first day of the snapshot month |
| customer_key | BIGINT | dim_customer |
| service_profile_key | BIGINT | dim_service_profile |
| tenure_months | INT | |
| tenure_band | NVARCHAR(10) | |
| addon_service_count | TINYINT | |
| monthly_charges | DECIMAL(8,2) | |
| total_charges | DECIMAL(10,2) | |
| total_charges_imputed | BIT | |
| churn_flag | BIT | |
| risk_score | DECIMAL(5,4) | churn probability; set by `python -m src.model.score` (Delta MERGE), NULL before it runs |
| risk_band | NVARCHAR(10) | high / medium / low; same MERGE |
| is_synthetic | BIT | false; true for any generated month |
| run_id | NVARCHAR(50) | warehouse build that wrote the row |

### fact_retention_action

DDL only; empty until Phase 7 (no Delta table yet, so the loader skips it).

| Column | Type |
|---|---|
| action_id | BIGINT PK |
| customer_key | BIGINT |
| action_date_key | INT |
| action_type | NVARCHAR(50) |
| outcome | NVARCHAR(50) |
| note | NVARCHAR(500) |
| logged_by | NVARCHAR(100) |
| created_at | DATETIME2 |

## Churn model scores

Delta table `data/model/customer_scores` (overwritten on every `python -m src.model.score`), loaded to `churn.customer_scores` in SQL Server by the loader. One row per customer; PK `customer_id`.

| Column | Type | Notes |
|---|---|---|
| customer_id | NVARCHAR(20) | business key |
| risk_score | DECIMAL(5,4) | probability of churn, rounded to 4 decimals |
| risk_band | NVARCHAR(10) | high (>= 0.60), medium (>= 0.30), else low; cut-offs are business choices (`src/model/score.py`) |
| reason_1, reason_2, reason_3 | NVARCHAR(100) | top features raising this customer's risk, e.g. `contract = Month-to-month`; NULL when fewer than 3 positive contributions |
| in_training_set | BIT | true when the model was fitted on this customer (the score is in-sample) |
| model_version | NVARCHAR(50) | `run_id` of the training run (`data/evidence/model_metrics.json`) |
| scored_at | DATETIME2 | |

## Supabase tables

Phase 7a, Postgres 17 on Supabase. Built by `backend/supabase/migrations/0001_schema.sql` (tables), `0002_security.sql` (RLS, grants, policies) and `0003_storage.sql` (bucket). Helper function `private.current_org_id()` (security definer, in the unexposed `private` schema) maps `auth.uid()` to the user's `profiles.org_id`; a user without a profile sees nothing. Migrations are tracked in `private.schema_migrations`. Every table has RLS on. Nothing is granted to `anon`; `authenticated` has only the grants below, and there is no DELETE anywhere. Pipeline outputs are written by the worker with the secret key or the postgres role.

| Table | Columns | What `authenticated` may do |
|---|---|---|
| organisations | id uuid PK, name, created_at | SELECT own organisation |
| profiles | user_id PK (references auth.users, cascade), org_id, role (analyst/manager), display_name, created_at | SELECT profiles of own organisation; no writes |
| uploads | id, org_id, uploaded_by, file_path (unique, must start with `<org_id>/`), original_name, size_bytes (1..5242880), snapshot_month (`YYYY-MM`), status (queued/processing/done/rejected/failed, default queued), status_reason, run_id, created_at, started_at, finished_at | SELECT own org; INSERT (org_id, uploaded_by, file_path, original_name, size_bytes, snapshot_month) with org match, uploaded_by = self, status queued, path under own org folder, fewer than 20 uploads in the last 24 hours |
| pipeline_runs | run_id PK, org_id, upload_id, snapshot_month, status, started_at, finished_at, stage_seconds jsonb, rows jsonb, dq_summary jsonb, recon_summary jsonb, model_version | SELECT own org |
| dq_results | PK (run_id, rule_id); org_id, description, columns, checked_rows, failing_rows, failing_rate, threshold, status, action, run_ts | SELECT own org |
| reconciliation_results | PK (run_id, check_id); org_id, description, expected, actual, difference, status, run_ts | SELECT own org |
| customer_scores | PK (org_id, snapshot_month, customer_id); risk_score numeric(5,4), risk_band (high/medium/low), reason_1..3, contract, internet_service, payment_method, tenure_months, tenure_band, monthly_charges, churned, model_version, scored_at | SELECT own org |
| segment_aggregates | PK (org_id, snapshot_month, segment_type, segment); customers, churned, churn_rate_pct, mrr, mrr_lost | SELECT own org |
| retention_actions | id, org_id, customer_id, snapshot_month, action_type (call/email/discount_offer/contract_upgrade_offer/tech_support_session/other), note (max 500), outcome (pending/retained/churned/no_response), created_by, created_at, updated_at | SELECT own org; INSERT (org_id, customer_id, snapshot_month, action_type, note, created_by) with org match, created_by = self and an existing `customer_scores` row for that org, customer and month; UPDATE (outcome, note, updated_at) on own rows only |

Migration `0004_hardening.sql` adds: foreign keys from the five output tables (`pipeline_runs` to `segment_aggregates`) to `organisations`; a limit of 20 uploads per organisation per rolling 24 hours, enforced in the `uploads` INSERT policy and in the `storage.objects` INSERT policy (via SECURITY DEFINER count helpers in `private`); and a retention-action INSERT rule that the customer must already have a `customer_scores` row for the same org and snapshot month. Storage bucket `uploads` is private, 5 MB limit, MIME `text/csv` and `application/vnd.ms-excel`; objects live at `<org_id>/<file>`; authenticated users can INSERT and SELECT only under their own org folder, with no update or delete. The RLS proof test writes `data/evidence/supabase_rls_test.json`.

The worker (`src/integration/supabase_worker.py`, Phase 7b) fills the output tables. Every row carries `org_id`. `dq_results` and `reconciliation_results` are keyed by `run_id` (a rejected run still gets its `dq_results`). `customer_scores` and `segment_aggregates` are replaced per (org_id, snapshot_month): delete, then insert, so re-processing a month is idempotent. `customer_scores` also carries the Silver fields contract, internet_service, payment_method, tenure_months, tenure_band, monthly_charges and churned. `segment_aggregates.segment_type` is one of overall (segment `All`), contract, internet_service, payment_method, tenure_band; customers, churned and churn_rate_pct come from Gold, `mrr` = sum(monthly_charges) and `mrr_lost` = sum(monthly_charges where churned) from Silver. `pipeline_runs` holds `stage_seconds` (seconds per stage: download, bronze, dq, silver, gold, reconciliation, score, supabase_write), `rows` (row counts per stage and table), `dq_summary` {pass, warn, fail}, `recon_summary` {pass, fail}, status done/rejected/failed. Spark JDBC inserts are not one transaction; an upload stays `processing` until every write succeeded. Each processed upload also appends one JSON line to `data/evidence/worker_runs.jsonl`, and the live test writes `data/evidence/worker_e2e_test.json`.

## Expected baseline figures
### Admin panel (migration 0005_admin.sql)

| Object | Definition | Access |
|---|---|---|
| private.platform_admins | user_id PK (references auth.users, cascade), created_at | No grants to anon or authenticated; not in the Data API. Filled only by the CLI `make-platform-admin` (postgres role) |
| profiles (new columns) | email text, is_active boolean default true, must_change_password boolean default false | SELECT also of own row (so a deactivated user can read why); UPDATE only `must_change_password`, only own row, only to false (column grant plus policy) |
| audit_log | id bigint identity PK, org_id (references organisations), actor_id (references auth.users, on delete set null), action (create_org/create_user/set_role/deactivate_user/reactivate_user/reset_password), target_user_id, details jsonb (never passwords), created_at | SELECT only, for an active manager of that org; written by the Edge Function with the secret key |
| private.is_manager(), private.is_platform_admin() | SECURITY DEFINER helpers based on `auth.uid()` | EXECUTE for authenticated |
| public.admin_is_platform_admin(uuid) | SECURITY DEFINER lookup used by the Edge Function | EXECUTE for service_role only |
| organisations | SELECT policy for platform admins (all organisations); since 0008 also requires `auth.jwt() ->> 'aal' = 'aal2'` | |
| profiles, audit_log (0008) | SELECT policies `profiles_select_platform`, `audit_log_select_platform`: platform admin and `aal2` session | Org-management data only; no platform-admin policy on any business table or storage |
| Triggers (0008) | `profiles_not_platform_admin` (BEFORE INSERT OR UPDATE OF user_id on profiles) and `platform_admin_has_no_profile` (BEFORE INSERT on private.platform_admins); SECURITY DEFINER, `search_path = ''`, EXECUTE revoked from public/anon/authenticated | An account is never both platform admin and organisation member. Existing rows are not re-checked |
| Migration 0009 | backfills `profiles.email` from `auth.users` where NULL; replaces `audit_log_action_check` to add `'set_name'`; backfills `details.actor_label` / `details.target_label` on existing audit rows | Data and constraint only; no new grants or policies. From now on the Edge Function writes both labels (never a password) |

`private.current_org_id()` now returns NULL for a deactivated profile, so every org-scoped policy returns nothing for that user even if a session survives.


Verified against `data/raw/Telco-Customer-Churn.csv` on 2026-11-12 with an independent Python count (also confirmed: 7,043 unique customerIDs, 11 blank TotalCharges all at tenure 0, tenure 0..72, MonthlyCharges 18.25..118.75). Reconciliation never uses these as its expected side; it computes from the source CSV.

Overall: 1,869 churned of 7,043 = 26.54%.

| Segment | Churned | Customers | Churn rate |
|---|---|---|---|
| InternetService: Fiber optic | 1,297 | 3,096 | 41.89% |
| InternetService: DSL | 459 | 2,421 | 18.96% |
| InternetService: No | 113 | 1,526 | 7.40% |
| Contract: Month-to-month | 1,655 | 3,875 | 42.71% |
| Contract: One year | 166 | 1,473 | 11.27% |
| Contract: Two year | 48 | 1,695 | 2.83% |
| PaymentMethod: Electronic check | 1,071 | 2,365 | 45.29% |
| PaymentMethod: Mailed check | 308 | 1,612 | 19.11% |
| PaymentMethod: Bank transfer (automatic) | 258 | 1,544 | 16.71% |
| PaymentMethod: Credit card (automatic) | 232 | 1,522 | 15.24% |
| SeniorCitizen = 1 | 476 | 1,142 | 41.68% |

Within each segmentation, churned sums to 1,869 and customers to 7,043. Tenure-band figures below come from the pipeline's Gold output (`gold_churn_by_tenure_band`, first verified load), not from an independent count:

| Segment | Churned | Customers | Churn rate |
|---|---|---|---|
| TenureBand: 0-12 | 1,037 | 2,186 | 47.44% |
| TenureBand: 13-24 | 294 | 1,024 | 28.71% |
| TenureBand: 25-48 | 325 | 1,594 | 20.39% |
| TenureBand: 49-72 | 213 | 2,239 | 9.51% |
