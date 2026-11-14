import os
from pathlib import Path

# PROJECT_ROOT = backend/pipeline/ (data, config, models, jars, .env live here).
# BACKEND_ROOT = backend/ (supabase/ lives there). REPO_ROOT = the repository root (docs/ lives there).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT.parent
REPO_ROOT = BACKEND_ROOT.parent
# All pipeline data (raw CSV, Delta tables, reports, model). The Databricks job points this at a
# Unity Catalog Volume, e.g. /Volumes/workspace/churn_spark/files; see src/config/storage.py.
DATA_ROOT = Path(os.environ["CHURN_DATA_ROOT"]) if os.environ.get("CHURN_DATA_ROOT") else PROJECT_ROOT / "data"
RAW_CSV = DATA_ROOT / "raw" / "Telco-Customer-Churn.csv"
BRONZE_PATH = DATA_ROOT / "bronze" / "telco_customers"

EXPECTED_RAW_COLUMNS = [
    "customerID", "gender", "SeniorCitizen", "Partner", "Dependents", "tenure",
    "PhoneService", "MultipleLines", "InternetService", "OnlineSecurity",
    "OnlineBackup", "DeviceProtection", "TechSupport", "StreamingTV",
    "StreamingMovies", "Contract", "PaperlessBilling", "PaymentMethod",
    "MonthlyCharges", "TotalCharges", "Churn",
]
# A file without Churn is "unlabelled": customers are scored, but there is no actual churn to report.
OPTIONAL_RAW_COLUMNS = ["Churn"]
REQUIRED_RAW_COLUMNS = [c for c in EXPECTED_RAW_COLUMNS if c not in OPTIONAL_RAW_COLUMNS]

METADATA_COLUMNS = ["_ingest_ts", "_source_file", "_batch_id"]

DQ_RULES_PATH = PROJECT_ROOT / "config" / "dq_rules.json"
DQ_RESULTS_PATH = DATA_ROOT / "quality" / "dq_results"
DQ_REPORT_JSON = DATA_ROOT / "quality" / "dq_report.json"

SILVER_PATH = DATA_ROOT / "silver" / "telco_customers"
GOLD_DIR = DATA_ROOT / "gold"

GOLD_OVERALL = "gold_churn_overall"
GOLD_SEGMENTS = {
    "gold_churn_by_internet_service": "internet_service",
    "gold_churn_by_contract": "contract",
    "gold_churn_by_payment_method": "payment_method",
    "gold_churn_by_tenure_band": "tenure_band",
}

RECON_RESULTS_PATH = DATA_ROOT / "reconciliation" / "reconciliation_results"
RECON_REPORT_JSON = DATA_ROOT / "reconciliation" / "reconciliation_report.json"

# Warehouse (star schema) and SQL Server serving copy
WAREHOUSE_DIR = DATA_ROOT / "warehouse"
SNAPSHOT_MONTH = "2026-10"  # assigned: the source file is undated
SQL_DDL_DIR = PROJECT_ROOT / "sql" / "ddl"
EVIDENCE_DIR = DATA_ROOT / "evidence"
SQLSERVER_REPORT_JSON = DATA_ROOT / "reconciliation" / "sqlserver_report.json"
MSSQL_JDBC_PACKAGE = "com.microsoft.sqlserver:mssql-jdbc:13.6.2.jre11"
POSTGRES_JDBC_PACKAGE = "org.postgresql:postgresql:42.7.4"
# Optional local copies of the jars above (+ Delta), git-ignored. When all are present, Spark
# starts without contacting Maven Central. Fill with: copy %USERPROFILE%\.ivy2\jars\*.jar jars\
JARS_DIR = PROJECT_ROOT / "jars"
REQUIRED_JARS = ("io.delta_delta-spark_2.12-3.3.3", "io.delta_delta-storage-3.3.3",
                 "org.antlr_antlr4-runtime-4.9.3", "com.microsoft.sqlserver_mssql-jdbc-13.6.2",
                 "org.postgresql_postgresql-42.7.4")

# Churn model (Phase 6)
# On Databricks the model lives in the Volume next to the data.
MODEL_DIR = (DATA_ROOT / "models" if os.environ.get("CHURN_DATA_ROOT") else PROJECT_ROOT / "models") / "churn_lr"
MODEL_METRICS_JSON = EVIDENCE_DIR / "model_metrics.json"
MODEL_SCORING_JSON = EVIDENCE_DIR / "model_scoring.json"
MODEL_SCORES_PATH = DATA_ROOT / "model" / "customer_scores"
