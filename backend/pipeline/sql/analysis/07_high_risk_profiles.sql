-- Question: Which service segments contain the most churned customers?
-- Grain: one row per combination of the five shown attributes (several dim_service_profile rows
-- roll up into one segment), top 10 by churned count, latest snapshot month.
-- Assumptions: profiles with fewer than 30 customers are ignored; ties on churned count are broken
-- by churn rate.
WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
),
profiles AS (
    SELECT
        p.contract, p.internet_service, p.payment_method, p.tech_support, p.online_security,
        COUNT(*)                        AS customers,
        SUM(CAST(f.churn_flag AS INT))  AS churned
    FROM churn.fact_customer_snapshot AS f
    JOIN latest AS l                     ON l.snapshot_date_key = f.snapshot_date_key
    JOIN churn.dim_service_profile AS p  ON p.service_profile_key = f.service_profile_key
    GROUP BY p.contract, p.internet_service, p.payment_method,
             p.tech_support, p.online_security
    HAVING COUNT(*) >= 30
)
SELECT TOP 10
    contract, internet_service, payment_method, tech_support, online_security,
    customers,
    churned,
    CAST(100.0 * churned / customers AS DECIMAL(5,2)) AS churn_rate_pct
FROM profiles
ORDER BY churned DESC, churn_rate_pct DESC;
