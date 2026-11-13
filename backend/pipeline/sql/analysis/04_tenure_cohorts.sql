-- Question: When in the customer lifetime does churn happen, and what does it cost?
-- Grain: one row per tenure band (0-12, 13-24, 25-48, 49-72 months), latest snapshot month.
-- Assumptions: mrr_lost = sum of monthly_charges of churned customers in the band; the cumulative
-- share runs from the shortest band to the longest and is the share of ALL churned customers.
WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
),
bands AS (
    SELECT
        f.tenure_band,
        MIN(f.tenure_months)            AS band_start,
        COUNT(*)                        AS customers,
        SUM(CAST(f.churn_flag AS INT))  AS churned,
        CAST(SUM(CASE WHEN f.churn_flag = 1 THEN f.monthly_charges ELSE 0 END) AS DECIMAL(12,2)) AS mrr_lost
    FROM churn.fact_customer_snapshot AS f
    JOIN latest AS l ON l.snapshot_date_key = f.snapshot_date_key
    GROUP BY f.tenure_band
)
SELECT
    tenure_band,
    customers,
    churned,
    CAST(100.0 * churned / customers AS DECIMAL(5,2))                           AS churn_rate_pct,
    mrr_lost,
    CAST(100.0 * SUM(churned) OVER (ORDER BY band_start) / SUM(churned) OVER () AS DECIMAL(5,2))
                                                                                AS cumulative_churned_share_pct
FROM bands
ORDER BY band_start;
