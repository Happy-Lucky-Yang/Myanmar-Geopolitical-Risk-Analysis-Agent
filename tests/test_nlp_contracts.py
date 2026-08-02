"""NLP contract and regression tests."""
import sys
from types import SimpleNamespace
from unittest.mock import patch


def test_sentiment_module_imports_without_optional_models():
    from analyzer.sentiment import SentimentAnalyzer

    analyzer = SentimentAnalyzer()
    assert 0.0 <= analyzer.analyze("和平合作取得进展", lang="zh") <= 1.0


def test_empty_text_ner_returns_stable_shape_without_loading_models():
    from analyzer.ner import NERExtractor

    assert NERExtractor().extract_entities("") == {
        "locations": [],
        "organizations": [],
        "persons": [],
        "events": [],
    }


def test_ner_batch_returns_one_stable_result_per_text(monkeypatch):
    from analyzer.ner import NERExtractor

    extractor = NERExtractor()
    monkeypatch.setattr(
        extractor,
        "extract_entities",
        lambda text: {"locations": [text], "organizations": [], "persons": [], "events": []},
    )

    result = extractor.extract_batch(["甲", "乙"])

    assert [item["locations"] for item in result] == [["甲"], ["乙"]]


def test_missing_english_model_has_actionable_error(monkeypatch):
    import analyzer.ner as ner_module

    monkeypatch.setattr(ner_module, "spacy", None)
    try:
        ner_module.NERExtractor().extract_entities("Myanmar conflict")
    except ImportError as exc:
        assert "spaCy" in str(exc)
    else:
        raise AssertionError("missing spaCy must be visible to callers")


def test_chinese_ner_maps_lac_tags_and_events(monkeypatch):
    import analyzer.ner as ner_module

    class FakeLAC:
        def __init__(self, mode):
            assert mode == "lac"

        def run(self, text):
            return [["昂山素季", "联合国", "掸邦"], ["PER", "ORG", "LOC"]]

    monkeypatch.setattr(ner_module, "LAC", FakeLAC)
    result = ner_module.NERExtractor().extract_entities("昂山素季与联合国关注掸邦冲突")

    assert result["persons"] == ["昂山素季"]
    assert result["organizations"] == ["联合国"]
    assert result["locations"] == ["掸邦"]
    assert result["events"] == ["冲突"]


def test_english_ner_maps_spacy_labels(monkeypatch):
    import analyzer.ner as ner_module

    class Entity:
        def __init__(self, text, label):
            self.text = text
            self.label_ = label

    class Model:
        def __call__(self, text):
            return type("Doc", (), {"ents": [
                Entity("Myanmar", "GPE"),
                Entity("United Nations", "ORG"),
                Entity("Aung San Suu Kyi", "PERSON"),
            ]})()

    class FakeSpacy:
        @staticmethod
        def load(name):
            assert name == "en_core_web_sm"
            return Model()

    monkeypatch.setattr(ner_module, "spacy", FakeSpacy())
    result = ner_module.NERExtractor().extract_entities(
        "Aung San Suu Kyi met the United Nations in Myanmar after the conflict."
    )

    assert result["locations"] == ["Myanmar"]
    assert result["organizations"] == ["United Nations"]
    assert result["persons"] == ["Aung San Suu Kyi"]
    assert result["events"] == ["conflict"]


def test_risk_scorer_uses_content_before_title():
    from analyzer.risk_scorer import RiskScorer

    scorer = RiskScorer(weights={
        "conflict_frequency": 0.30,
        "sentiment_avg": 0.25,
        "nightlight_change": 0.20,
        "refugee_change": 0.15,
        "event_severity": 0.10,
    })
    result = scorer.compute_daily_risk([
        {
            "title": "今日地区新闻简报",
            "content": "缅甸北部发生武装冲突",
            "sentiment_score": 0.2,
        }
    ])

    assert result["conflict_count"] == 1


def test_missing_vader_lexicon_does_not_trigger_hidden_download(monkeypatch):
    import analyzer.sentiment as sentiment_module

    downloads = []

    def missing_lexicon():
        raise LookupError("missing vader lexicon")

    monkeypatch.setattr(sentiment_module, "_VADER_AVAILABLE", True)
    monkeypatch.setattr(sentiment_module, "SentimentIntensityAnalyzer", missing_lexicon, raising=False)
    monkeypatch.setitem(sys.modules, "nltk", SimpleNamespace(download=lambda *args, **kwargs: downloads.append(args)))

    analyzer = sentiment_module.SentimentAnalyzer()
    analyzer.analyze("A neutral scheduled meeting.", lang="en")

    assert downloads == []


def test_chinese_model_failure_reports_fallback_provenance(monkeypatch):
    import analyzer.sentiment as sentiment_module

    class BrokenSnowNLP:
        def __init__(self, text):
            raise RuntimeError("model failure")

    monkeypatch.setattr(sentiment_module, "SnowNLP", BrokenSnowNLP)
    result = sentiment_module.SentimentAnalyzer().get_risk_sentiment(
        "和平合作取得进展", lang="zh"
    )

    assert result["source"] == "fallback"


def test_scheduler_reuses_unified_pipeline():
    from data.scheduler import CrawlerScheduler

    expected = {
        "success": True,
        "processed_count": 3,
        "risk": {"risk_score": 20.0, "risk_level": "低风险"},
        "warnings": [],
    }
    scheduler = CrawlerScheduler(config={"scheduler": {"enabled": False}})
    with patch("pipeline.run_pipeline", return_value=expected) as run:
        scheduler._run_analysis_job()

    run.assert_called_once_with(source_mode="existing", include_llm=False, persist=True)
    assert scheduler._last_analysis_result["success"] is True
    assert scheduler._last_analysis_result["news_count"] == 3


def test_scheduler_reports_failure_when_every_crawler_fails():
    from data.scheduler import CrawlerScheduler

    scheduler = CrawlerScheduler(config={"scheduler": {"enabled": False}})
    with patch("data.crawler.NewsCrawler", side_effect=RuntimeError("zh failed")), \
         patch("data.myanmar_now_crawler.get_english_crawler", side_effect=RuntimeError("en failed")), \
         patch("data.rss_crawler.get_rss_crawler", side_effect=RuntimeError("rss failed")):
        scheduler._run_crawl_job()

    assert scheduler._last_crawl_result["success"] is False
    assert len(scheduler._last_crawl_result["errors"]) == 3
