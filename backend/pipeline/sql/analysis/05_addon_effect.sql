-- Question: Do customers with more add-on services churn less?
-- Grain: one row per addon_service_count (0-6), internet customers only, latest snapshot month.
-- Assumptions: add-ons only exist for internet customers, so customers with internet_service = 'No'
-- are excluded; the relationship is an association, not proof that add-ons reduce churn.
WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
)
SELECT
    f.addon_service_count,
    COUNT(*)                                                                  AS customers,
    SUM(CAST(f.churn_flag AS INT))                                            AS churned,
    CAST(100.0 * SUM(CAST(f.churn_flag AS INT)) / COUNT(*) AS DECIMAL(5,2))   AS churn_rate_pct
FROM churn.fact_customer_snapshot AS f
JOIN latest AS l                     ON l.snapshot_date_key = f.snapshot_date_key
JOIN churn.dim_service_profile AS p  ON p.service_profile_key = f.service_profile_key
WHERE p.internet_service <> 'No'
GROUP BY f.addon_service_count
ORDER BY f.addon_service_count;
