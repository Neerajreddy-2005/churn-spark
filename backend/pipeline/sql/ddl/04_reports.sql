IF OBJECT_ID('churn.dq_results', 'U') IS NULL
CREATE TABLE churn.dq_results (
    run_id         NVARCHAR(50)  NOT NULL,
    rule_id        NVARCHAR(20)  NOT NULL,
    column_name    NVARCHAR(100) NULL,
    checked_rows   INT           NOT NULL,
    failing_rows   INT           NOT NULL,
    threshold      NVARCHAR(50)  NULL,
    status         NVARCHAR(10)  NOT NULL,   -- pass / warn / fail
    action         NVARCHAR(10)  NULL,       -- fail / warn / fix
    run_ts         DATETIME2     NOT NULL,
    PRIMARY KEY (run_id, rule_id)
);
GO

IF OBJECT_ID('churn.reconciliation_results', 'U') IS NULL
CREATE TABLE churn.reconciliation_results (
    run_id         NVARCHAR(50)  NOT NULL,
    check_id       NVARCHAR(20)  NOT NULL,
    description    NVARCHAR(200) NULL,
    expected       NVARCHAR(MAX) NULL,
    actual         NVARCHAR(MAX) NULL,
    difference     FLOAT         NULL,
    status         NVARCHAR(10)  NOT NULL,   -- pass / fail
    run_ts         DATETIME2     NOT NULL,
    PRIMARY KEY (run_id, check_id)
);
