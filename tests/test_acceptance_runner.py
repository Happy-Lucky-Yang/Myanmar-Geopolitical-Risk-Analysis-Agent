"""Acceptance report runner tests."""
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ner_records_are_evaluated_with_micro_f1():
    from scripts.evaluate_acceptance import evaluate_ner_records

    records = [{
        "text": "掸邦发生冲突",
        "entities": {"locations": ["掸邦"], "organizations": [], "persons": [], "events": ["冲突"]},
    }]

    class Extractor:
        def extract_entities(self, text):
            return {"locations": ["掸邦"], "organizations": [], "persons": [], "events": []}

    result = evaluate_ner_records(records, Extractor())

    assert result["total"] == 1
    assert result["metrics"]["precision"] == 1.0
    assert result["metrics"]["recall"] == 0.5
    assert result["passed"] is False


def test_acceptance_script_runs_from_project_root_and_reports_pending_ner(tmp_path):
    output = tmp_path / "acceptance.json"

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/evaluate_acceptance.py"), "--output", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["sentiment"]["passed"] is True
    assert report["ner"]["status"] == "pending"
