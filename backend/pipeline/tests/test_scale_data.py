import csv

from src.evidence.scale_data import NOISE, generate

HEADER = ("customerID,gender,SeniorCitizen,Partner,Dependents,tenure,PhoneService,MultipleLines,InternetService,"
          "OnlineSecurity,OnlineBackup,DeviceProtection,TechSupport,StreamingTV,StreamingMovies,Contract,"
          "PaperlessBilling,PaymentMethod,MonthlyCharges,TotalCharges,Churn")
ROWS = [
    "A-1,Female,0,Yes,No,1,No,No phone service,DSL,No,Yes,No,No,No,No,Month-to-month,Yes,Electronic check,29.85,29.85,No",
    "B-2,Male,0,No,No,34,Yes,No,DSL,Yes,No,Yes,No,No,No,One year,No,Mailed check,56.95,1889.5,No",
    "C-3,Male,1,No,No,0,Yes,No,DSL,Yes,No,Yes,No,No,No,Two year,No,Mailed check,40.00, ,Yes",
]


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_synthetic_rows_keep_rules_and_are_reproducible(tmp_path):
    src = tmp_path / "real.csv"
    src.write_text(HEADER + "\n" + "\n".join(ROWS) + "\n", encoding="utf-8")
    info = generate(500, tmp_path / "a.csv", source=src)
    generate(500, tmp_path / "b.csv", source=src)
    rows = read(tmp_path / "a.csv")
    real = {r["tenure"] + r["Contract"]: r for r in read(src)}  # (tenure, contract) is unique in the fixture

    assert info["rows"] == len(rows) == 500 and info["source_rows"] == 3
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()  # same seed, same file
    assert len({r["customerID"] for r in rows}) == 500 and rows[0]["customerID"] == "SYN00000001"
    for r in rows:
        base = real[r["tenure"] + r["Contract"]]
        assert {k: v for k, v in r.items() if k not in ("customerID", "MonthlyCharges", "TotalCharges")} == \
               {k: v for k, v in base.items() if k not in ("customerID", "MonthlyCharges", "TotalCharges")}
        factor = float(r["MonthlyCharges"]) / float(base["MonthlyCharges"])
        assert 1 - NOISE - 0.001 <= factor <= 1 + NOISE + 0.001
        assert len(r["MonthlyCharges"].split(".")[1]) == 2
        if base["TotalCharges"].strip():
            assert abs(float(r["TotalCharges"]) / float(base["TotalCharges"]) - factor) < 0.001
        else:
            assert r["TotalCharges"] == base["TotalCharges"]  # blank stays blank (the dq10 case)
