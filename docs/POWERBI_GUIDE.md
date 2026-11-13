# Power BI guide (Phase 5c)

You build this report yourself in Power BI Desktop, on the star schema the pipeline loaded into SQL Server. Allow 2–3 hours. Every number you see must match `docs/INSIGHTS.md`; step 6 checks that.

## 1. Connect

1. Power BI Desktop → **Get data → SQL Server**.
2. Server: `localhost\SQLEXPRESS` · Database: `churn_db` · Data connectivity mode: **Import** → OK.
3. Credentials: **Windows** → "Use my current credentials" → Connect.
   If it complains about encryption or the certificate, click OK on the "unencrypted connection" prompt (local machine only).
4. In the Navigator, tick these tables (all in schema `churn`), then **Load**:
   - `fact_customer_snapshot`, `dim_customer`, `dim_service_profile`, `dim_date`
   - `dq_results`, `reconciliation_results`

You do not need the five `gold_*` tables; your measures recompute the same numbers from the fact table, which is the point.

## 2. Relationships (Model view)

Delete any relationships Power BI guessed, then create these three. Each is **many-to-one, single direction** (fact → dimension):

| From (many) | To (one) |
|---|---|
| `fact_customer_snapshot[customer_key]` | `dim_customer[customer_key]` |
| `fact_customer_snapshot[service_profile_key]` | `dim_service_profile[service_profile_key]` |
| `fact_customer_snapshot[snapshot_date_key]` | `dim_date[date_key]` |

This is the star schema: one fact table in the middle, dimensions around it. Be ready to explain why `contract` and `payment_method` live in `dim_service_profile` (they change month to month; customer attributes do not).

## 3. Measures

Create a table for measures: **Home → Enter data** → name it `_Measures` → Load. Then select it and use **New measure** for each:

```dax
Customers = COUNTROWS ( fact_customer_snapshot )

Churned = CALCULATE ( [Customers], fact_customer_snapshot[churn_flag] = TRUE () )

Churn Rate = DIVIDE ( [Churned], [Customers] )

MRR = SUM ( fact_customer_snapshot[monthly_charges] )

MRR Lost = CALCULATE ( [MRR], fact_customer_snapshot[churn_flag] = TRUE () )

MRR Lost % = DIVIDE ( [MRR Lost], [MRR] )

Share of Churners =
DIVIDE ( [Churned], CALCULATE ( [Churned], REMOVEFILTERS ( dim_service_profile ) ) )

DQ Rules Passed =
CALCULATE ( COUNTROWS ( dq_results ), dq_results[status] = "pass" )

Recon Checks Passed =
CALCULATE ( COUNTROWS ( reconciliation_results ), reconciliation_results[status] = "pass" )
```

Formatting: Churn Rate, MRR Lost %, Share of Churners → Percentage, 2 decimals. MRR, MRR Lost → Decimal number, 2 decimals, thousands separator. `churn_flag` is a SQL `BIT`, so it arrives as True/False; that is why the measures filter on `TRUE ()` instead of summing it.

## 4. Pages

**Page 1 — Overview**
- Five cards: Customers, Churned, Churn Rate, MRR Lost, MRR Lost %.
- Clustered bar: Churn Rate by `dim_service_profile[contract]`.
- Clustered bar: Churn Rate by `dim_service_profile[internet_service]`.
- Clustered bar: Churn Rate by `dim_service_profile[payment_method]`, with Share of Churners in the tooltip.
- Column chart: Churn Rate by `fact_customer_snapshot[tenure_band]` (sort the axis by tenure_band ascending).

**Page 2 — Where churn concentrates**
- Matrix: rows `contract`, columns `internet_service`, values Churn Rate and Customers. Add conditional formatting (background colour scale) on Churn Rate.
- Table: contract, internet_service, payment_method, tech_support, online_security, Customers, Churned, Churn Rate; sort by Churned descending; visual-level filter Customers ≥ 30. This reproduces query 07 in `docs/INSIGHTS.md`.
- Slicers: `dim_customer[senior_citizen]`, `dim_customer[partner]`, `dim_service_profile[paperless_billing]`.

**Page 3 — Data you can trust**
- Cards: DQ Rules Passed, Recon Checks Passed.
- Table from `dq_results`: rule_id, status, failing_rows, checked_rows, action (dq10 shows warn, 11 rows: the blank TotalCharges fixed in Silver).
- Table from `reconciliation_results`: check_id, status, expected, actual.
- A text box: "Every number in this report is reconciled to the source CSV by the pipeline (exact counts, independent Python path)."

Keep the design plain: one accent colour for churn, grey for everything else, titles that state the finding (e.g. "Month-to-month contracts churn at 42.7%"), not "Chart 1".

## 5. Save

- Save as `Churn-Spark.pbix` **outside the repo** (for example `Documents\Churn-Spark.pbix`); `.pbix` files are git-ignored and can be large.
- **File → Export → Export to PDF** → save to `docs/powerbi/Churn-Spark-report.pdf`.
- Take one screenshot per page → `docs/powerbi/page1-overview.png`, `page2-segments.png`, `page3-trust.png`.

## 6. Verify (this is your evidence)

Check each value on the report against `docs/INSIGHTS.md` / the pipeline output. All must match exactly:

| Visual | Expected |
|---|---|
| Customers | 7,043 |
| Churned | 1,869 |
| Churn Rate | 26.54% |
| MRR Lost / MRR Lost % | 139,130.85 / 30.50% |
| Month-to-month churn rate | 42.71% |
| Fiber optic churn rate | 41.89% |
| Electronic check churn rate / share of churners | 45.29% / 57.30% |
| Tenure 0-12 churn rate | 47.44% |
| Top segment in the page-2 table | 651 of 989 (65.82%) |

If any value differs, do not adjust the visual. Tell Claude which one; a mismatch means a measure or relationship is wrong, and finding it is part of the work.

When all match, tell Claude "Power BI done". The screenshots and PDF become entries in `docs/EVIDENCE.md`.
