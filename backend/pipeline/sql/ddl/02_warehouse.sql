-- Star schema. Delta (data/warehouse) is the source of truth; these tables are a serving copy
-- that the loader truncates and reloads. Because of the truncate there are NO FOREIGN KEY
-- constraints: referential integrity is proven by reconciliation checks rc13 instead.
IF OBJECT_ID('churn.dim_customer', 'U') IS NULL
CREATE TABLE churn.dim_customer (
    customer_key        BIGINT        NOT NULL PRIMARY KEY,
    customer_id         NVARCHAR(20)  NOT NULL UNIQUE,
    gender              NVARCHAR(10)  NULL,
    senior_citizen      BIT           NULL,
    partner             BIT           NULL,
    dependents          BIT           NULL,
    first_seen_date_key INT           NOT NULL,
    last_seen_date_key  INT           NOT NULL
);
GO

IF OBJECT_ID('churn.dim_service_profile', 'U') IS NULL
CREATE TABLE churn.dim_service_profile (
    service_profile_key BIGINT        NOT NULL PRIMARY KEY,   -- xxhash64 of the attributes
    contract            NVARCHAR(20)  NULL,
    internet_service    NVARCHAR(20)  NULL,
    phone_service       BIT           NULL,
    multiple_lines      NVARCHAR(30)  NULL,
    online_security     NVARCHAR(30)  NULL,
    online_backup       NVARCHAR(30)  NULL,
    device_protection   NVARCHAR(30)  NULL,
    tech_support        NVARCHAR(30)  NULL,
    streaming_tv        NVARCHAR(30)  NULL,
    streaming_movies    NVARCHAR(30)  NULL,
    paperless_billing   BIT           NULL,
    payment_method      NVARCHAR(50)  NULL
);
GO

IF OBJECT_ID('churn.dim_date', 'U') IS NULL
CREATE TABLE churn.dim_date (
    date_key        INT          NOT NULL PRIMARY KEY,   -- yyyymmdd
    full_date       DATE         NOT NULL UNIQUE,
    [year]          SMALLINT     NOT NULL,
    [quarter]       TINYINT      NOT NULL,
    [month]         TINYINT      NOT NULL,
    month_name      NVARCHAR(10) NOT NULL,
    year_month      CHAR(7)      NOT NULL,               -- yyyy-mm
    is_month_start  BIT          NOT NULL
);
GO

IF OBJECT_ID('churn.fact_customer_snapshot', 'U') IS NULL
CREATE TABLE churn.fact_customer_snapshot (
    snapshot_date_key     INT           NOT NULL,        -- first day of the snapshot month
    customer_key          BIGINT        NOT NULL,
    service_profile_key   BIGINT        NOT NULL,
    tenure_months         INT           NOT NULL,
    tenure_band           NVARCHAR(10)  NOT NULL,
    addon_service_count   TINYINT       NOT NULL,
    monthly_charges       DECIMAL(8,2)  NOT NULL,
    total_charges         DECIMAL(10,2) NOT NULL,
    total_charges_imputed BIT           NOT NULL,
    churn_flag            BIT           NOT NULL,
    risk_score            DECIMAL(5,4)  NULL,            -- NULL until Phase 6
    risk_band             NVARCHAR(10)  NULL,            -- NULL until Phase 6
    is_synthetic          BIT           NOT NULL,
    run_id                NVARCHAR(50)  NOT NULL,
    CONSTRAINT pk_fact_customer_snapshot PRIMARY KEY CLUSTERED (snapshot_date_key, customer_key)
);
GO

IF OBJECT_ID('churn.fact_retention_action', 'U') IS NULL
CREATE TABLE churn.fact_retention_action (   -- empty until Phase 7
    action_id       BIGINT         NOT NULL PRIMARY KEY,
    customer_key    BIGINT         NOT NULL,
    action_date_key INT            NOT NULL,
    action_type     NVARCHAR(50)   NULL,
    outcome         NVARCHAR(50)   NULL,
    note            NVARCHAR(500)  NULL,
    logged_by       NVARCHAR(100)  NULL,
    created_at      DATETIME2      NULL
);
