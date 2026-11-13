-- Churn model scores (Phase 6). Serving copy of the Delta table data/model/customer_scores;
-- the loader truncates and reloads it.
IF OBJECT_ID('churn.customer_scores', 'U') IS NULL
CREATE TABLE churn.customer_scores (
    customer_id      NVARCHAR(20)  NOT NULL PRIMARY KEY,
    risk_score       DECIMAL(5,4)  NOT NULL,   -- probability of churn
    risk_band        NVARCHAR(10)  NOT NULL,   -- high / medium / low
    reason_1         NVARCHAR(100) NULL,
    reason_2         NVARCHAR(100) NULL,
    reason_3         NVARCHAR(100) NULL,
    in_training_set  BIT           NOT NULL,   -- true: the model was fitted on this customer
    model_version    NVARCHAR(50)  NOT NULL,   -- run_id of the training run
    scored_at        DATETIME2     NOT NULL
);
GO
