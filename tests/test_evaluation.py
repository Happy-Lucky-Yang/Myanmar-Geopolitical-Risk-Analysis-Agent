"""Tests for repeatable human-label acceptance metrics."""
import json
from pathlib import Path

import pytest


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


def gold_record(text='fixture', group=None, language='en'):
    return {'text': text, 'event_group_id': group, 'language': language,
            'entities': {'locations': ['Myanmar'], 'organizations': [], 'persons': [], 'events': []}}


def test_group_partition_is_disjoint_stable_and_rejects_fake_independence():
    from scripts.evaluate_acceptance import grouped_partition
    rows = [gold_record(f'report-{i}-{j}', f'event-{i}') for i in range(10) for j in range(2)]
    calibration, left = grouped_partition(rows, 'calibration')
    holdout, right = grouped_partition(rows, 'holdout')
    assert set(calibration).isdisjoint(holdout) and len(calibration) + len(holdout) == 20
    assert {rows[i]['event_group_id'] for i in calibration}.isdisjoint({rows[i]['event_group_id'] for i in holdout})
    reversed_indices, reversed_meta = grouped_partition(list(reversed(rows)), 'holdout')
    assert {list(reversed(rows))[i]['text'] for i in reversed_indices} == {rows[i]['text'] for i in holdout}
    assert left['assignment_sha256'] == right['assignment_sha256'] == reversed_meta['assignment_sha256']
    with pytest.raises(ValueError):
        grouped_partition([gold_record()], 'holdout')
    with pytest.raises(ValueError):
        grouped_partition(rows + [gold_record(rows[0]['text'], 'another-event')], 'holdout')


def test_ner_baseline_cannot_claim_holdout_and_errors_do_not_leak_text():
    from scripts.evaluate_acceptance import evaluate_ner_records
    from types import SimpleNamespace
    rows = [gold_record('private-fixture')] * 60
    extractor = SimpleNamespace(extract_entities=lambda text: rows[0]['entities'], last_backend='fixture')
    result = evaluate_ner_records(rows, extractor)
    assert result['metrics']['f1'] == 1 and not result['passed']
    assert result['per_language']['en']['total'] == 60
    assert result['per_type']['locations']['tp'] == 60 and result['backends'] == {'fixture': 60}
    def fail(text):
        raise RuntimeError(text)
    extractor.extract_entities = fail
    result = evaluate_ner_records(rows[:1], extractor)
    assert result['errors'] == [{'record': 1, 'error_type': 'RuntimeError'}]
    assert 'private-fixture' not in json.dumps(result)


def test_ner_backend_fallback_and_log_privacy(monkeypatch, caplog):
    import analyzer.ner as ner
    extractor = ner.NERExtractor()
    monkeypatch.setattr(ner, 'LAC', object())
    monkeypatch.setattr(ner, 'pseg', None)
    def fail(text):
        raise RuntimeError('private-fixture')
    monkeypatch.setattr(extractor, '_extract_zh_lac', fail)
    assert '缅甸' in extractor.extract_entities('缅甸发生冲突')['locations']
    assert extractor.last_backend == 'dictionary_regex'
    assert 'private-fixture' not in caplog.text
    assert not any(extractor.extract_entities(' ').values())
    assert extractor.last_backend == 'empty_input'
    monkeypatch.setattr(extractor, '_detect_language', fail)
    with pytest.raises(RuntimeError):
        extractor.extract_entities('fixture')
    assert extractor.last_backend == 'failed'


@pytest.mark.parametrize('predicted', [None, [], {'locations': 'Myanmar'},
    {'locations': [None], 'organizations': [], 'persons': [], 'events': []}])
def test_ner_bad_predictions_are_counted_without_aborting(predicted):
    from types import SimpleNamespace
    from scripts.evaluate_acceptance import evaluate_ner_records
    extractor = SimpleNamespace(extract_entities=lambda text: predicted, last_backend='stale')
    result = evaluate_ner_records([gold_record()], extractor)
    assert result['errors'] == [{'record': 1, 'error_type': 'ValueError'}]
    assert result['backends'] == {'failed': 1} and result['metrics']['fn'] == 1


def test_ner_language_failure_and_duplicate_holdout_cannot_pass():
    from types import SimpleNamespace
    from scripts.evaluate_acceptance import evaluate_ner_records
    def fail(text):
        raise RuntimeError('private-fixture')
    extractor = SimpleNamespace(_detect_language=fail, extract_entities=lambda text: gold_record()['entities'])
    result = evaluate_ner_records([gold_record(language=None)], extractor)
    assert result['errors'] == [{'record': 1, 'error_type': 'RuntimeError'}]
    assert 'private-fixture' not in json.dumps(result)
    rows = [gold_record(f'event-{i}', f'event-{i}') for i in range(5) for _ in range(60)]
    result = evaluate_ner_records(rows, extractor, 'holdout')
    assert result['total'] == 60 and result['unique_texts'] == 1 and not result['passed']
    rows = [gold_record(f'report-{i}-{j}', f'event-{i}') for i in range(5) for j in range(50)]
    result = evaluate_ner_records(rows, extractor, 'holdout')
    assert result['unique_texts'] == 50 and result['passed']


def test_acceptance_cli_refuses_overwrite_before_loading_models(tmp_path):
    from scripts.evaluate_acceptance import main
    output = tmp_path / 'preserve.json'
    output.write_text('preserve', encoding='utf-8')
    with pytest.raises(SystemExit):
        main(['--output', str(output)])
    assert output.read_text(encoding='utf-8') == 'preserve'


def test_frozen_repository_nlp_baseline_reports_not_independent_acceptance(tmp_path):
    from scripts.evaluate_acceptance import main
    output = tmp_path / 'nlp-baseline.json'
    assert main(['--ner-gold', str(ROOT / 'data/static/ner_annotations/ner_gold_batch1.json'),
                 '--output', str(output)]) == 2
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['ner']['total'] == 60 and report['ner']['status'] == 'not_accepted'
    assert not report['passed'] and report['ner']['input_sha256']
    assert report['code_sha256'] and report['packages'] and report['ner']['backends']
    assert all('text' not in row for row in report['sentiment']['mismatches'])


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
