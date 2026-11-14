import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config.storage import cache, read_delta, uncache
from src.config.settings import (
    BRONZE_PATH, DQ_REPORT_JSON, DQ_RESULTS_PATH, DQ_RULES_PATH, EXPECTED_RAW_COLUMNS,
)
from src.ingestion.spark_session import get_spark
from src.quality.report import write_results
from src.quality.rules import RULE_TYPES

logger = logging.getLogger(__name__)

ACTIONS = ("fail", "warn", "fix")


class DataQualityError(Exception):
    pass


def load_rules(path=DQ_RULES_PATH):
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    seen = set()
    for rule in rules:
        rid = rule.get("id")
        if rid in seen:
            raise ValueError(f"Duplicate rule id: {rid}")
        seen.add(rid)
        if rule.get("type") not in RULE_TYPES:
            raise ValueError(f"Rule {rid}: unknown type {rule.get('type')!r}")
        if rule.get("action") not in ACTIONS:
            raise ValueError(f"Rule {rid}: unknown action {rule.get('action')!r}")
    return rules


def rule_status(failing, checked, threshold, action):
    # Compare the exact rate: rounding first would let 1 bad row in millions pass a 0 threshold.
    rate = failing / checked if checked else 0.0
    if rate <= threshold:
        return "pass"
    return "fail" if action == "fail" else "warn"


def run_checks(df, rules, run_id):
    run_ts = datetime.now(timezone.utc).isoformat()
    df = cache(df)
    try:
        total = df.count()
        results = []
        for rule in rules:
            failing = RULE_TYPES[rule["type"]](df, rule)
            if rule["type"] == "schema":
                checked = len(EXPECTED_RAW_COLUMNS)
            elif rule["type"] == "row_count_min":
                checked = 1
            else:
                checked = total
            rate = round(failing / checked, 6) if checked else 0.0
            threshold = float(rule.get("max_failing_rate", 0.0))
            status = rule_status(failing, checked, threshold, rule["action"])
            results.append({
                "run_id": run_id,
                "rule_id": rule["id"],
                "description": rule["description"],
                "columns": ",".join(rule["columns"]),
                "checked_rows": checked,
                "failing_rows": failing,
                "failing_rate": rate,
                "threshold": threshold,
                "status": status,
                "action": rule["action"],
                "run_ts": run_ts,
            })
            logger.info("%s %s: %d/%d failing", rule["id"], status, failing, checked)
        return results
    finally:
        uncache(df)


def enforce(results):
    failed = [r["rule_id"] for r in results if r["status"] == "fail"]
    if failed:
        raise DataQualityError(f"Data quality failed: {', '.join(failed)}")


def run_dq(spark, bronze_path=BRONZE_PATH, rules_path=DQ_RULES_PATH,
           results_path=DQ_RESULTS_PATH, report_json=DQ_REPORT_JSON, run_id=None):
    run_id = run_id or uuid.uuid4().hex
    rules = load_rules(rules_path)
    df = read_delta(spark, bronze_path)
    results = run_checks(df, rules, run_id)
    write_results(spark, results, results_path, report_json)
    enforce(results)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    start = time.perf_counter()
    spark = get_spark()
    code = 0
    results = []
    try:
        results = run_dq(spark)
    except DataQualityError as e:
        print(e)
        code = 1
    finally:
        spark.stop()
    for r in results:
        print(f"{r['rule_id']:<7}{r['status']:<6}{r['failing_rows']}/{r['checked_rows']}  {r['action']}")
    print(f"Elapsed seconds: {time.perf_counter() - start:.1f}")
    sys.exit(code)
