-- Question: What if some month-to-month customers moved to one-year contracts?
-- Grain: one row (the scenario), latest snapshot month.
-- Assumptions: migrated customers then churn at the one-year segment's historical rate; selection
-- effects are ignored (customers who accept a one-year contract may differ from those who would
-- not); illustrative, not causal. Revenue retained = churned customers avoided x the average
-- monthly charge of month-to-month churners. Change @migrate_pct to try another share.
DECLARE @migrate_pct DECIMAL(5,2) = 10.00;

WITH latest AS (
    SELECT MAX(snapshot_date_key) AS snapshot_date_key
    FROM churn.fact_customer_snapshot
),
seg AS (
    SELECT
        p.contract,
        COUNT(*)                        AS customers,
        SUM(CAST(f.churn_flag AS INT))  AS churned,
        AVG(CASE WHEN f.churn_flag = 1 THEN CAST(f.monthly_charges AS DECIMAL(18,6)) END) AS avg_churn_charge
    FROM churn.fact_customer_snapshot AS f
    JOIN latest AS l                     ON l.snapshot_date_key = f.snapshot_date_key
    JOIN churn.dim_service_profile AS p  ON p.service_profile_key = f.service_profile_key
    WHERE p.contract IN ('Month-to-month', 'One year')
    GROUP BY p.contract
),
inputs AS (
    SELECT
        m.customers                    AS mtm_customers,
        1.0 * m.churned / m.customers  AS mtm_rate,
        1.0 * o.churned / o.customers  AS one_year_rate,
        m.avg_churn_charge             AS mtm_churner_avg_charge
    FROM seg AS m
    CROSS JOIN seg AS o
    WHERE m.contract = 'Month-to-month' AND o.contract = 'One year'
)
SELECT
    @migrate_pct                                                    AS migrate_pct,
    mtm_customers,
    CAST(100.0 * mtm_rate AS DECIMAL(5,2))                          AS mtm_churn_rate_pct,
    CAST(100.0 * one_year_rate AS DECIMAL(5,2))                     AS one_year_churn_rate_pct,
    CAST(mtm_churner_avg_charge AS DECIMAL(8,2))                    AS mtm_churner_avg_charge,
    CAST(mtm_customers * @migrate_pct / 100.0 AS DECIMAL(10,1))     AS customers_migrated,
    CAST(mtm_customers * (@migrate_pct / 100.0) * (mtm_rate - one_year_rate) AS DECIMAL(10,1))
                                                                    AS churned_avoided,
    CAST(mtm_customers * (@migrate_pct / 100.0) * (mtm_rate - one_year_rate) * mtm_churner_avg_charge
         AS DECIMAL(12,2))                                          AS monthly_revenue_retained
FROM inputs;
