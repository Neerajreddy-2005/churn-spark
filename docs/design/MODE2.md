# Mode 2: bring your own churn dataset (design, for approval)

Status: built and tested (migrations 0010 and 0011). Mode 1 (certified Telco layout) is unchanged.

Changes made while building, compared with the proposal below:
- **Auto-train:** a file trains straight away when its churn column is clear (found by name, two values) and all checks pass. Otherwise it waits for a review. "Change columns" retrains at any time.
- **Storage:** two tables, not three. Model results live in `datasets.result`; scores are in `dataset_scores`.
- **Pages:** results appear in the same Overview and At-risk pages as the Telco data, through one Data picker. The Datasets page holds exploration, model details and Change columns.
- **Actions:** retention actions also work on dataset customers (`retention_actions.dataset_id`, migration 0011).
- **Leakage guard:** a second tier. A single-column AUC of 0.99 or higher removes the column; 0.90 or higher flags it.

## Goal

Any customer-churn table, whether telecom, bank, SaaS or e-commerce, gets:
1. an automatic data exploration,
2. general data checks,
3. a churn model trained on that company's own columns, with an honest holdout score,
4. an at-risk list with reasons.

A file that is not churn data gets the exploration only, with a clear message.

## Flow

```
Upload --> auto-detect
   |-- columns match the Telco layout --> Mode 1 (unchanged)
   '-- anything else --> PROFILE (worker)
                            |  types, missing %, distinct counts, value spreads
                            |  suggests: ID column, target column, columns to leave out (with reasons)
                            v
                      status "needs review" --> REVIEW page (manager/analyst confirms)
                            |  ID column . target column + which value means "left"
                            |  columns left out (pre-ticked suggestions, can change)
                            |  or "this is not churn data" -> exploration only
                            v
                      TRAIN + SCORE (worker)
                            |  checks -> clean -> 80/20 stratified split -> model -> holdout metrics
                            |  leakage guard -> score every row -> top 3 reasons
                            v
                      Dataset page: exploration . model card . at-risk list
```

Two steps instead of one because target detection cannot be fully automatic: in `sales.csv`, `Sales Channel` has exactly two values but is not a churn label. The system suggests and a person confirms.

## Suggestions the profiler makes (each one shown with its reason)

| Detected | Rule | Example from the test files |
|---|---|---|
| Target | Name matches churn/exited/attrition/left/cancelled/churned and the column has exactly 2 values | `Exited`, `Churn` |
| ID | Unique on every row; name contains id/number/key | `CustomerId`, `RowNumber` |
| Leave out: identifier | Unique or nearly unique values | `RowNumber`, `Order ID` |
| Leave out: personal | Name looks like name/surname/email/phone/address | `Surname` |
| Leave out: constant | One value only | `Count`, `Country`, `State` |
| Leave out: too many categories | Text column with more than 50 distinct values | `City`, `Lat Long` |
| Leave out: leakage | Name derived from the target (churn score/reason/label/value), or a single column predicts the target almost perfectly (one-column AUC 0.99 or higher) | `Churn Label`, `Churn Score`, `Churn Reason` |

## Data checks (general version of the 20 Telco rules)

- ID column not blank and unique.
- Target has exactly 2 values; the user picks which one means "left".
- At least 200 rows, with at least 30 in the smaller class, to train. Otherwise the file gets exploration only.
- At most 100 columns and 5 MB, like today.
- Missing values: numbers are filled with the median plus a "was missing" flag; text becomes "(missing)". The share filled is reported.
- Reconciliation, general version: source rows = Bronze = Silver = scored rows; target counts match the source; every ID is scored exactly once.

## Model

- Spark MLlib logistic regression, the same family as Mode 1, so every score can be explained per customer.
- Text columns: the top 20 categories, the rest grouped as "other". Numbers are scaled.
- Reported on the 20% holdout: AUC, precision-recall AUC, base churn rate, and the share of churners reached by calling the riskiest 20% (against 20% for a random pick).
- **Leakage guard**: if the trained model's holdout AUC is 0.99 or higher, the results are marked "suspiciously perfect: check for columns that reveal the answer". They are never presented as a good model.
- Scores: high / medium / low with the same cut-offs as Mode 1. Reasons come from the largest per-customer contributions, as in Mode 1.

## Automatic data exploration (also for non-churn files)

For each column, by type:
- **Number**: min, quartiles, max, mean, missing %, and a 10-bin histogram.
- **Text**: distinct count and top 10 values with counts.
- **With a target**: churn rate per category or per numeric bin, sorted into "biggest differences first". These are the "insights".
- **Privacy**: values of identifier and personal columns are never stored or shown, only their counts.

## Storage (one new Supabase migration, same RLS pattern as today)

- `datasets`: upload, org, kind (`telco` / `generic`), status, profile (jsonb), review choices (jsonb).
- `dataset_models`: metrics, top drivers, leakage findings.
- `dataset_scores`: org, dataset, row ID, risk score, band, 3 reasons, actual label (nullable).
- Column names are never used in SQL. Spark renames them internally to `c0..cN` and keeps a display map, so a hostile header cannot inject anything.

## Website

- Upload: unchanged for the user; the file type is detected automatically.
- New **Datasets** area: list, then review page, then dataset page (Exploration . Model . At-risk).
- The Telco Overview and at-risk list stay as they are.

## Proof (the evidence rule)

Integration run on the 5 real Kaggle files (local only, never committed). Recorded in `data/evidence/mode2_datasets.json`:
- file hash, rows, columns,
- suggestions, plus which ones were right against my manual reading of each file,
- checks,
- holdout metrics,
- leakage findings,
- run times.

Expected outcomes to verify, not assume:

| File | Expected outcome |
|---|---|
| Bank | Trains. `Surname`, `RowNumber` and `CustomerId` are left out. |
| SaaS | Trains. This public file is known to be easy to predict, so the leakage guard may trigger. Report whatever happens honestly. |
| E-commerce | Trains after filling the missing values. |
| IBM 33-col | Trains only after the 5 leakage columns are removed; this proves the guard works. |
| Sales | Exploration only. `Sales Channel` is not accepted as a churn target. |

## Build order

| Step | Work | Model |
|---|---|---|
| M2.1 | Profiler, suggestions and checks (pipeline) plus unit tests | Opus design, Sonnet build |
| M2.2 | Training, leakage guard, scoring and general reconciliation, plus tests on the 5 files | Opus |
| M2.3 | Supabase migration (needs your OK before applying to production), worker integration, live tests | Opus |
| M2.4 | Datasets pages (review, exploration, model, at-risk) | Sonnet build, Opus review |
| M2.5 | Full test pass on localhost, your check, then one push after you say yes | |
