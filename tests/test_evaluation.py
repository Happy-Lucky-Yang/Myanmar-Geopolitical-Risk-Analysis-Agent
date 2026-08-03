"""Tests for repeatable human-label acceptance metrics."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_entity_micro_metrics_counts_exact_matches():
    from analyzer.evaluation import entity_micro_metrics

    gold = [{"locations": ["掸邦"], "organizations": ["联合国"], "persons": [], "events": ["冲突"]}]
    predicted = [{"locations": ["掸邦"], "organizations": [], "persons": [], "events": ["冲突", "空袭"]}]

    metrics = entity_micro_metrics(gold, predicted)

    assert metrics == {"precision": 2 / 3, "recall": 2 / 3, "f1": 2 / 3, "tp": 2, "fp": 1, "fn": 1}


def test_sentiment_gold_dataset_has_required_size_and_balance():
    records = json.loads((ROOT / "tests/fixtures/sentiment_gold.json").read_text(encoding="utf-8"))

    assert len(records) >= 30
    assert {record["risk_level"] for record in records} == {"low", "medium", "high"}
    assert {record["language"] for record in records} == {"zh", "en"}


def test_offline_sentiment_fallback_meets_acceptance_threshold(monkeypatch):
    import analyzer.sentiment as sentiment_module
    from analyzer.evaluation import evaluate_sentiment_records
    from analyzer.sentiment import SentimentAnalyzer

    monkeypatch.setattr(sentiment_module, "SnowNLP", None)
    records = json.loads((ROOT / "tests/fixtures/sentiment_gold.json").read_text(encoding="utf-8"))
    analyzer = SentimentAnalyzer()
    analyzer._vader = None

    result = evaluate_sentiment_records(records, analyzer)

    assert result["total"] >= 30
    assert result["agreement"] >= 0.70
