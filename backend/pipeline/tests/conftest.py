import pytest

from src.ingestion.spark_session import get_spark


@pytest.fixture(scope="session")
def spark():
    session = get_spark(app_name="churn-spark-tests", master="local[1]")
    yield session
    session.stop()
