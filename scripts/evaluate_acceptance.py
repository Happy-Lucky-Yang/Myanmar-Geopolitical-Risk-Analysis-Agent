"""Generate repeatable NLP acceptance metrics from human-labelled JSON."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyzer.evaluation import entity_micro_metrics, evaluate_sentiment_records


def evaluate_ner_records(records, extractor):
    predictions = [extractor.extract_entities(record.get("text", "")) for record in records]
    gold = [record.get("entities", {}) for record in records]
    metrics = entity_micro_metrics(gold, predictions)
    return {
        "total": len(records),
        "metrics": metrics,
        "passed": len(records) >= 50 and metrics["f1"] >= 0.70,
    }


def build_parser():
    parser = argparse.ArgumentParser(description="NER/情感人工标注验收")
    parser.add_argument("--ner-gold", type=Path, help="历史组提供的 NER 金标准 JSON")
    parser.add_argument(
        "--sentiment-gold",
        type=Path,
        default=ROOT / "tests/fixtures/sentiment_gold.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data/processed/acceptance_report.json",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from analyzer.sentiment import get_sentiment_analyzer

    sentiment_records = json.loads(args.sentiment_gold.read_text(encoding="utf-8"))
    sentiment = evaluate_sentiment_records(sentiment_records, get_sentiment_analyzer())
    sentiment["passed"] = sentiment["total"] >= 30 and sentiment["agreement"] >= 0.70

    if args.ner_gold:
        from analyzer.ner import get_ner_extractor
        ner_records = json.loads(args.ner_gold.read_text(encoding="utf-8"))
        ner = evaluate_ner_records(ner_records, get_ner_extractor())
    else:
        ner = {"status": "pending", "passed": False, "message": "等待至少 50 条人工标注"}

    report = {"sentiment": sentiment, "ner": ner, "passed": sentiment["passed"] and ner["passed"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
