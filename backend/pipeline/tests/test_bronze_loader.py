import pytest
from pyspark.sql.types import StringType

from src.config.settings import EXPECTED_RAW_COLUMNS, METADATA_COLUMNS, RAW_CSV
from src.ingestion.bronze_loader import load_bronze

HEADER = ",".join(EXPECTED_RAW_COLUMNS)
ROWS = [
    "7590-VHVEG,Female,0,Yes,No,1,No,No phone service,DSL,No,Yes,No,No,No,No,Month-to-month,Yes,Electronic check,29.85,29.85,No",
    "5575-GNVDE,Male,0,No,No,34,Yes,No,DSL,Yes,No,Yes,No,No,No,One year,No,Mailed check,56.95,1889.5,No",
    "4472-LVYGI,Female,0,Yes,Yes,0,No,No phone service,DSL,Yes,No,Yes,Yes,Yes,No,Two year,Yes,Bank transfer (automatic),52.55, ,No",
]


def write_csv(tmp_path, header=HEADER, rows=ROWS, name="telco.csv"):
    path = tmp_path / name
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def bronze(spark, tmp_path):
    csv = write_csv(tmp_path)
    out = tmp_path / "bronze"
    count = load_bronze(spark, csv, out, batch_id="batch-1")
    return count, spark.read.format("delta").load(out.as_posix())


def test_row_count(bronze):
    count, df = bronze
    assert count == 3
    assert df.count() == 3


def test_columns_in_order(bronze):
    _, df = bronze
    assert df.columns == EXPECTED_RAW_COLUMNS + METADATA_COLUMNS


def test_raw_columns_are_string(bronze):
    _, df = bronze
    assert all(isinstance(df.schema[c].dataType, StringType) for c in EXPECTED_RAW_COLUMNS)


def test_blank_total_charges_preserved(bronze):
    _, df = bronze
    row = df.filter(df.customerID == "4472-LVYGI").first()
    assert row["TotalCharges"] == " "
    assert row["tenure"] == "0"


def test_metadata_values(bronze):
    _, df = bronze
    row = df.first()
    assert row["_source_file"] == "telco.csv"
    assert row["_batch_id"] == "batch-1"
    assert row["_ingest_ts"] is not None


def test_rerun_does_not_duplicate(spark, tmp_path):
    csv = write_csv(tmp_path)
    out = tmp_path / "bronze"
    assert load_bronze(spark, csv, out) == 3
    assert load_bronze(spark, csv, out) == 3


def test_missing_column_raises(spark, tmp_path):
    cols = EXPECTED_RAW_COLUMNS[:-1]
    rows = [",".join(r.split(",")[:-1]) for r in ROWS]
    csv = write_csv(tmp_path, ",".join(cols), rows)
    with pytest.raises(ValueError, match="Churn"):
        load_bronze(spark, csv, tmp_path / "bronze")


def test_extra_column_raises(spark, tmp_path):
    rows = [r + ",5" for r in ROWS]
    csv = write_csv(tmp_path, HEADER + ",numAdminTickets", rows)
    with pytest.raises(ValueError, match="numAdminTickets"):
        load_bronze(spark, csv, tmp_path / "bronze")


@pytest.mark.skipif(not RAW_CSV.exists(), reason="real CSV not present")
@pytest.mark.integration
def test_real_csv(spark, tmp_path):
    out = tmp_path / "bronze"
    assert load_bronze(spark, RAW_CSV, out) == 7043
    df = spark.read.format("delta").load(out.as_posix())
    blanks = df.filter(df.TotalCharges == " ")
    assert blanks.count() == 11
    assert blanks.filter(blanks.tenure != "0").count() == 0
