"""Repeatable metrics for human-labelled NLP acceptance data."""
from typing import Dict, Iterable, List


ENTITY_FIELDS = ("locations", "organizations", "persons", "events")


def _entity_set(record: Dict) -> set:
    values = set()
    for field in ENTITY_FIELDS:
        for value in record.get(field, []) or []:
            normalized = str(value).strip().casefold()
            if normalized:
                values.add((field, normalized))
    return values


def entity_micro_metrics(gold_records: List[Dict], predictions: List[Dict]) -> Dict:
    """Calculate entity-level micro precision, recall and F1 by entity type."""
    tp = fp = fn = 0
    total = max(len(gold_records), len(predictions))
    for index in range(total):
        gold = _entity_set(gold_records[index] if index < len(gold_records) else {})
        predicted = _entity_set(predictions[index] if index < len(predictions) else {})
        tp += len(gold & predicted)
        fp += len(predicted - gold)
        fn += len(gold - predicted)

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def evaluate_sentiment_records(records: Iterable[Dict], analyzer) -> Dict:
    """Compare risk-level predictions with human labels."""
    rows = list(records)
    correct = 0
    mismatches = []
    for index, record in enumerate(rows):
        result = analyzer.get_risk_sentiment(
            record.get("text", ""),
            lang=record.get("language"),
        )
        predicted = result["risk_level"]
        expected = record.get("risk_level")
        if predicted == expected:
            correct += 1
        else:
            mismatches.append({
                "index": index,
                "expected": expected,
                "predicted": predicted,
                "text": record.get("text", ""),
            })
    total = len(rows)
    return {
        "total": total,
        "correct": correct,
        "agreement": correct / total if total else 0.0,
        "mismatches": mismatches,
    }
