-- Question: Which contract and internet service combinations churn the most?
-- Grain: one row per contract x internet_service cell, latest snapshot month.
-- Assumptions: only cells with at least 50 customers are shown, so rates are not driven by tiny
-- groups; rate_rank 1 is the highest churn rate (ties share a rank).
WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
),
cells AS (
    SELECT
        p.contract,
        p.internet_service,
        COUNT(*)                        AS customers,
        SUM(CAST(f.churn_flag AS INT))  AS churned
    FROM churn.fact_customer_snapshot AS f
    JOIN latest AS l                     ON l.snapshot_date_key = f.snapshot_date_key
    JOIN churn.dim_service_profile AS p  ON p.service_profile_key = f.service_profile_key
    GROUP BY p.contract, p.internet_service
    HAVING COUNT(*) >= 50
)
SELECT
    contract,
    internet_service,
    customers,
    churned,
    CAST(100.0 * churned / customers AS DECIMAL(5,2))             AS churn_rate_pct,
    RANK() OVER (ORDER BY 1.0 * churned / customers DESC)         AS rate_rank
FROM cells
ORDER BY rate_rank, contract, internet_service;
