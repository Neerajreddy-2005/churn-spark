import pytest

from src.config import storage
from src.config.settings import BRONZE_PATH, DATA_ROOT, GOLD_DIR, MODEL_SCORES_PATH, SILVER_PATH, WAREHOUSE_DIR


@pytest.fixture
def uc(monkeypatch):
    monkeypatch.setattr(storage, "UC_SCHEMA", "workspace.churn_spark")


@pytest.mark.parametrize("path, table", [
    (BRONZE_PATH, "bronze_telco_customers"),
    (SILVER_PATH, "silver_telco_customers"),
    (GOLD_DIR / "gold_churn_overall", "gold_churn_overall"),
    (DATA_ROOT / "quality" / "dq_results", "quality_dq_results"),
    (DATA_ROOT / "reconciliation" / "reconciliation_results", "reconciliation_results"),
    (WAREHOUSE_DIR / "dim_customer", "warehouse_dim_customer"),
    (MODEL_SCORES_PATH, "model_customer_scores"),
])
def test_paths_map_to_unity_catalog_tables(uc, path, table):
    assert storage.table_name(path) == f"workspace.churn_spark.{table}"


def test_path_outside_data_root_is_rejected(uc, tmp_path):
    with pytest.raises(ValueError, match="not under DATA_ROOT"):
        storage.table_name(tmp_path / "bronze")
