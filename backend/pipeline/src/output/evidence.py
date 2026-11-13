import json
from pathlib import Path

from src.config.settings import EVIDENCE_DIR


def write_evidence(name, payload, evidence_dir=EVIDENCE_DIR):
    path = Path(evidence_dir).resolve() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
