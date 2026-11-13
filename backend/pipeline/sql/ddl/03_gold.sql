IF OBJECT_ID('churn.gold_churn_overall', 'U') IS NULL
CREATE TABLE churn.gold_churn_overall (
    segment        NVARCHAR(50)  NOT NULL PRIMARY KEY,
    customers      INT           NOT NULL,
    churned        INT           NOT NULL,
    churn_rate_pct DECIMAL(5,2)  NOT NULL
);

GO

IF OBJECT_ID('churn.gold_churn_by_internet_service', 'U') IS NULL
CREATE TABLE churn.gold_churn_by_internet_service (
    segment        NVARCHAR(50)  NOT NULL PRIMARY KEY,
    customers      INT           NOT NULL,
    churned        INT           NOT NULL,
    churn_rate_pct DECIMAL(5,2)  NOT NULL
);

GO

IF OBJECT_ID('churn.gold_churn_by_contract', 'U') IS NULL
CREATE TABLE churn.gold_churn_by_contract (
    segment        NVARCHAR(50)  NOT NULL PRIMARY KEY,
    customers      INT           NOT NULL,
    churned        INT           NOT NULL,
    churn_rate_pct DECIMAL(5,2)  NOT NULL
);

GO

IF OBJECT_ID('churn.gold_churn_by_payment_method', 'U') IS NULL
CREATE TABLE churn.gold_churn_by_payment_method (
    segment        NVARCHAR(50)  NOT NULL PRIMARY KEY,
    customers      INT           NOT NULL,
    churned        INT           NOT NULL,
    churn_rate_pct DECIMAL(5,2)  NOT NULL
);

GO

IF OBJECT_ID('churn.gold_churn_by_tenure_band', 'U') IS NULL
CREATE TABLE churn.gold_churn_by_tenure_band (
    segment        NVARCHAR(50)  NOT NULL PRIMARY KEY,
    customers      INT           NOT NULL,
    churned        INT           NOT NULL,
    churn_rate_pct DECIMAL(5,2)  NOT NULL
);
