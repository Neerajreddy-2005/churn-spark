-- Question: How many customers do we have and what share churned?
-- Grain: one row for the latest snapshot month in churn.fact_customer_snapshot.
-- Assumptions: the snapshot month is assigned (the source file is undated); churn_flag = 1 means churned.
WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
)
SELECT
    d.year_month                                                             AS snapshot_month,
    COUNT(*)                                                                 AS customers,
    SUM(CAST(f.churn_flag AS INT))                                           AS churned,
    CAST(100.0 * SUM(CAST(f.churn_flag AS INT)) / COUNT(*) AS DECIMAL(5,2))  AS churn_rate_pct
FROM churn.fact_customer_snapshot AS f
JOIN latest AS l          ON l.snapshot_date_key = f.snapshot_date_key
JOIN churn.dim_date AS d  ON d.date_key = f.snapshot_date_key
GROUP BY d.year_month;
