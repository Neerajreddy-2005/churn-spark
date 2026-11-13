-- Question: How much monthly recurring revenue (MRR) is lost to churn?
-- Grain: one row for the latest snapshot month.
-- Assumptions: MRR = sum of monthly_charges; the MRR of churned customers is treated as the
-- monthly revenue lost; charges other than monthly_charges are not modelled.
WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
),
snap AS (
    SELECT f.monthly_charges, CAST(f.churn_flag AS INT) AS churned
    FROM churn.fact_customer_snapshot AS f
    JOIN latest AS l ON l.snapshot_date_key = f.snapshot_date_key
)
SELECT
    CAST(SUM(monthly_charges) AS DECIMAL(12,2))                                        AS total_mrr,
    CAST(SUM(CASE WHEN churned = 1 THEN monthly_charges ELSE 0 END) AS DECIMAL(12,2))  AS churned_mrr,
    CAST(100.0 * SUM(CASE WHEN churned = 1 THEN monthly_charges ELSE 0 END)
         / SUM(monthly_charges) AS DECIMAL(5,2))                                       AS churned_share_pct,
    CAST(AVG(CASE WHEN churned = 1 THEN monthly_charges END) AS DECIMAL(8,2))          AS avg_charge_churned,
    CAST(AVG(CASE WHEN churned = 0 THEN monthly_charges END) AS DECIMAL(8,2))          AS avg_charge_retained
FROM snap;
