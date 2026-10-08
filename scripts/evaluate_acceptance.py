"""Generate repeatable NLP acceptance metrics from human-labelled JSON."""
import argparse
from collections import Counter
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analyzer.evaluation import ENTITY_FIELDS, entity_micro_metrics, evaluate_sentiment_records
from utils.data_contract import fingerprint, utc_now


def grouped_partition(records, split='baseline', seed='42'):
    """只按人工事件组划分；缺组时不能用每篇报道代替独立事件。"""
    if not isinstance(records, list):
        raise ValueError('NER金标准必须是数组')
    for index, row in enumerate(records):
        if (not isinstance(row, dict) or not isinstance(row.get('text'), str) or not row['text'].strip()
                or not isinstance(row.get('entities'), dict)
                or any(not isinstance(row['entities'].get(k), list)
                       or any(not isinstance(v, str) or not v.strip() for v in row['entities'][k]) for k in ENTITY_FIELDS)):
            raise ValueError(f'NER记录{index + 1}的正文或实体类型无效')
    if split == 'baseline':
        return list(range(len(records))), {'status': 'baseline_only', 'groups': None}
    if split not in {'calibration', 'holdout'}:
        raise ValueError('无效评测分区')
    groups, texts = {}, {}
    for index, row in enumerate(records):
        group = row.get('event_group_id')
        if not isinstance(group, str) or not group.strip():
            raise ValueError(f'NER记录{index + 1}缺少人工event_group_id，不生成伪留出集')
        group = group.strip()
        text_id = fingerprint(' '.join(row['text'].split()).casefold())
        if text_id in texts and texts[text_id] != group:
            raise ValueError('重复正文被分到不同事件组，需要人工复核')
        texts[text_id] = group
        groups.setdefault(group, []).append(index)
    ordered = sorted(groups, key=lambda g: fingerprint([seed, g]))
    if len(ordered) < 5:
        raise ValueError('至少需要5个人工事件组才能划分校准与留出集')
    held_out = set(ordered[:max(1, (len(ordered) + 4) // 5)])
    selected = sorted(i for group, ids in groups.items() if (group in held_out) == (split == 'holdout') for i in ids)
    assignment = {fingerprint(group): 'holdout' if group in held_out else 'calibration' for group in groups}
    return selected, {'status': split, 'seed': seed, 'groups': len(groups),
                      'selected_groups': len(held_out) if split == 'holdout' else len(groups) - len(held_out),
                      'assignment_sha256': fingerprint(assignment)}


def evaluate_ner_records(records, extractor, split='baseline'):
    indices, partition = grouped_partition(records, split)
    predictions, gold, languages, failures, errors = [], [], [], [], []
    backends = Counter()
    for index in indices:
        row = records[index]
        language = row.get('language') if row.get('language') in {'zh', 'en'} else 'unspecified'
        try:
            if language == 'unspecified' and hasattr(extractor, '_detect_language'):
                detected = extractor._detect_language(row['text'])
                language = detected if detected in {'zh', 'en'} else 'unspecified'
            predicted = extractor.extract_entities(row['text'])
            if (not isinstance(predicted, dict)
                    or any(not isinstance(predicted.get(key), list)
                           or any(not isinstance(v, str) or not v.strip() for v in predicted[key])
                           for key in ENTITY_FIELDS)):
                raise ValueError('NER输出不符合实体列表契约')
            backend = getattr(extractor, 'last_backend', 'unspecified')
        except Exception as exc:
            predicted = {}
            backend = 'failed'
            errors.append({'record': index + 1, 'error_type': type(exc).__name__})
        backends[backend] += 1
        predictions.append(predicted)
        gold.append(row['entities'])
        languages.append(language)
        metric = entity_micro_metrics([row['entities']], [predicted])
        if metric['fp'] or metric['fn']:
            failures.append({'record': index + 1, 'language': language, 'metrics': metric})
    metrics = entity_micro_metrics(gold, predictions)
    per_language = {}
    for language in sorted(set(languages)):
        positions = [i for i, value in enumerate(languages) if value == language]
        per_language[language] = {'total': len(positions), 'metrics': entity_micro_metrics(
            [gold[i] for i in positions], [predictions[i] for i in positions])}
    per_type = {key: entity_micro_metrics([{key: row.get(key, [])} for row in gold],
                                         [{key: row.get(key, [])} for row in predictions]) for key in ENTITY_FIELDS}
    unique_texts = len({fingerprint(' '.join(records[i]['text'].split()).casefold()) for i in indices})
    eligible = split == 'holdout' and unique_texts >= 50 and not errors
    return {'total': len(indices), 'unique_texts': unique_texts, 'dataset_total': len(records), 'metrics': metrics,
            'per_language': per_language, 'per_type': per_type, 'partition': partition, 'backends': dict(backends),
            'input_sha256': fingerprint(records), 'evaluated_sha256': fingerprint([records[i] for i in indices]),
            'failures': failures, 'errors': errors, 'passed': eligible and metrics['f1'] >= .70,
            'status': 'holdout_measured' if eligible else 'not_accepted',
            'note': '基线或样本不足不算验收；分组不能证明未泄漏，留出集不得用于调词典或参数。'}


def build_parser():
    parser = argparse.ArgumentParser(description="NER/情感人工标注验收")
    parser.add_argument("--ner-gold", type=Path, help="历史组提供的 NER 金标准 JSON")
    parser.add_argument('--ner-split', choices=['baseline', 'calibration', 'holdout'], default='baseline')
    parser.add_argument(
        "--sentiment-gold",
        type=Path,
        default=ROOT / "tests/fixtures/sentiment_gold.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help='全新报告路径；不指定时仅输出计数，不覆盖既有报告',
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        parser.error('禁止覆盖既有评测报告，请指定全新路径')
    from analyzer.sentiment import get_sentiment_analyzer

    sentiment_records = json.loads(args.sentiment_gold.read_text(encoding="utf-8"))
    sentiment = evaluate_sentiment_records(sentiment_records, get_sentiment_analyzer())
    sentiment["threshold_met"] = sentiment["total"] >= 30 and sentiment["agreement"] >= 0.70
    sentiment['passed'] = sentiment['threshold_met']
    sentiment['status'] = 'baseline_only'
    sentiment['note'] = '此门槛只检验固定基线回归，不证明真实效果改善或独立留出正确率。'
    sentiment['input_sha256'] = fingerprint(sentiment_records)
    sentiment['mismatches'] = [{k: v for k, v in row.items() if k != 'text'} for row in sentiment['mismatches']]

    if args.ner_gold:
        from analyzer.ner import get_ner_extractor
        ner_records = json.loads(args.ner_gold.read_text(encoding="utf-8"))
        ner = evaluate_ner_records(ner_records, get_ner_extractor(), args.ner_split)
    else:
        ner = {"status": "pending", "passed": False, "message": "等待按人工事件组划分、含至少50条不同正文的独立留出集"}

    packages = {}
    for package in ('LAC', 'paddlepaddle', 'jieba', 'spacy', 'en-core-web-sm', 'snownlp', 'nltk'):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    report = {'created_at': utc_now().isoformat(), 'evaluation_version': 'nlp-eval-v2',
              'python': platform.python_version(), 'packages': packages,
              'code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                  'analyzer/ner.py', 'analyzer/sentiment.py', 'analyzer/evaluation.py', 'scripts/evaluate_acceptance.py')},
              'sentiment': sentiment, 'ner': ner, 'passed': sentiment['passed'] and ner['passed']}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'evaluation_version': report['evaluation_version'], 'passed': report['passed'],
                      'ner': {k: v for k, v in ner.items() if k not in {'failures', 'errors'}},
                      'sentiment': {k: v for k, v in sentiment.items() if k != 'mismatches'}}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
