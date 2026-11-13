-- Question: Which payment methods are linked to churn and how much revenue do they lose?
-- Grain: one row per payment method, latest snapshot month.
-- Assumptions: mrr_lost = sum of monthly_charges of churned customers; share_of_churned_pct is the
-- method's share of all churned customers; payment method is an association, not a cause.
WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
),
methods AS (
    SELECT
        p.payment_method,
        COUNT(*)                        AS customers,
        SUM(CAST(f.churn_flag AS INT))  AS churned,
        CAST(SUM(CASE WHEN f.churn_flag = 1 THEN f.monthly_charges ELSE 0 END) AS DECIMAL(12,2)) AS mrr_lost
    FROM churn.fact_customer_snapshot AS f
    JOIN latest AS l                     ON l.snapshot_date_key = f.snapshot_date_key
    JOIN churn.dim_service_profile AS p  ON p.service_profile_key = f.service_profile_key
    GROUP BY p.payment_method
)
SELECT
    payment_method,
    customers,
    churned,
    CAST(100.0 * churned / customers AS DECIMAL(5,2))             AS churn_rate_pct,
    mrr_lost,
    CAST(100.0 * churned / SUM(churned) OVER () AS DECIMAL(5,2))  AS share_of_churned_pct
FROM methods
ORDER BY churn_rate_pct DESC;
