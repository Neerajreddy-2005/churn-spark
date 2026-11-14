import json
from types import SimpleNamespace

import pytest

from src import pipeline


@pytest.fixture
def fake_stages(monkeypatch, tmp_path):
    calls = []

    def stages(spark, run_id, snapshot_month):
        def ok(name):
            return lambda: calls.append(name) or {"rows": 1}

        def boom():
            calls.append("silver")
            raise ValueError("3 rows have null total_charges")

        return {"bronze": ok("bronze"), "dq": ok("dq"), "silver": boom, "gold": ok("gold")}

    monkeypatch.setattr(pipeline, "_stage_functions", stages)
    monkeypatch.setattr(pipeline, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "write_evidence",
                        lambda name, payload, d: (d / name).write_text(json.dumps(payload)))
    return calls, tmp_path


SPARK = SimpleNamespace(version="test")


def test_success_writes_last_run_and_appends_log(fake_stages):
    calls, tmp = fake_stages
    log = tmp / "runs.jsonl"
    for _ in range(2):
        summary = pipeline.run_pipeline(SPARK, stages=["bronze", "dq"], run_log=log)
    assert calls == ["bronze", "dq", "bronze", "dq"]
    assert summary["status"] == "ok" and [s["stage"] for s in summary["stages"]] == ["bronze", "dq"]
    assert all(s["status"] == "ok" and s["seconds"] >= 0 for s in summary["stages"])
    assert len(log.read_text().splitlines()) == 2
    assert json.loads((tmp / pipeline.LAST_RUN).read_text())["run_id"] == summary["run_id"]


def test_failure_stops_records_and_reraises(fake_stages):
    calls, tmp = fake_stages
    log = tmp / "runs.jsonl"
    with pytest.raises(ValueError, match="null total_charges"):
        pipeline.run_pipeline(SPARK, stages=["bronze", "silver", "gold"], run_log=log)
    assert calls == ["bronze", "silver"]  # gold never ran
    logged = json.loads(log.read_text())
    assert logged["status"] == "failed"
    assert logged["stages"][-1]["stage"] == "silver"
    assert logged["stages"][-1]["error"].startswith("ValueError: 3 rows")


def test_no_sqlserver_drops_only_that_stage():
    assert pipeline.STAGES[-1] == "sqlserver"
    assert [s for s in pipeline.STAGES if s != "sqlserver"] == \
        ["bronze", "dq", "silver", "gold", "reconcile", "warehouse", "train", "score"]


def test_bad_snapshot_month_is_rejected():
    with pytest.raises(SystemExit):
        pipeline.main(["--snapshot-month", "Oct-2026"])
