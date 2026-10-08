"""日历时间、版本隔离、零值和预测门槛的离线回归。"""
from datetime import date, timedelta
import json

import pytest

from utils.data_contract import (RISK_VERSION, business_date, time_window, window_utc,
                                 valid_risk_record, select_history, calendar_series, coverage_metadata)


def record(day, value=0, **extra):
    return {"date": day, "risk_score": value, "run_kind": "existing",
            "algorithm_version": RISK_VERSION, **extra}


def test_calendar_window_and_timezone():
    assert business_date(None) is None
    assert business_date("") is None
    assert business_date("2026-01-01T18:00:00Z") == date(2026, 1, 2)
    assert time_window(3, "2026-01-02") == (date(2025, 12, 31), date(2026, 1, 3))
    assert window_utc(1, "2026-01-02")[0].isoformat() == "2026-01-01T17:30:00+00:00"
    for days in (0, -1, True, 3661, "30"):
        with pytest.raises(ValueError):
            time_window(days)


def test_invalid_record_never_becomes_today():
    for value in (None, "garbage", "2026-02-30"):
        assert not valid_risk_record(record(value))
    for value in (None, float("nan"), float("inf"), True, -1, 101):
        assert not valid_risk_record(record("2026-01-01", value))
    assert valid_risk_record(record("2026-01-01", 0))


def test_latest_timestamp_uses_instant_not_lexicographic_order():
    rows = [record("2026-01-01", 20, recorded_at="2026-01-01T12:00:00+06:30"),
            record("2026-01-01", 30, recorded_at="2026-01-01T08:00:00Z")]
    assert select_history(rows, 1, "2026-01-01")[0]["risk_score"] == 30


def test_history_is_calendar_bounded_and_domain_isolated():
    rows = [record("2025-01-01", 1), record("2026-01-01", 0),
            record("2026-01-03", 50), record("2026-01-02", 90, run_kind="demo"),
            record("2026-01-02", 80, run_kind="manual"),
            record("2026-01-02", 70, algorithm_version="old"),
            {"date": "2026-01-02", "risk_score": 60}]
    selected = select_history(rows, 3, "2026-01-03")
    assert calendar_series(selected, 3, "2026-01-03")[1] == [0, None, 50]
    assert coverage_metadata(rows[:3], 3, "2026-01-03")["valid_days"] == 2
    assert len(select_history(rows, 3, "2026-01-03", include_legacy=True)) == 1


def test_file_storage_domains_bad_lines_and_idempotency(tmp_path):
    from analyzer.data_loader import DataLoader
    loader = DataLoader(processed_dir=str(tmp_path))
    for mode in ("demo", "manual", "legacy", "existing"):
        loader.append_risk_score("2026-01-01", 0, "低风险", run_kind=mode, input_hash="abc")
    loader.append_risk_score("2026-01-01", 0, "低风险", run_kind="existing", input_hash="abc")
    path = tmp_path / "daily_risk.jsonl"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    with path.open("a", encoding="utf-8") as handle:
        handle.write('null\n{"date":null,"risk_score":10}\nbroken\n')
    assert len(loader.load_risk_history(1, "2026-01-01")) == 1
    assert len(loader.last_read_errors) == 3
    assert len(loader.load_risk_history(1, "2026-01-01", include_legacy=True)) == 1


def test_dedup_preserves_sources_and_does_not_mutate():
    from analyzer.data_loader import DataLoader
    original = [{"content": "same news", "pub_time": "2026-01-01", "source": "A", "url": "https://a.test/?utm_source=x",
                 "source_urls": ["https://b.test/"]},
                {"content": "same news", "pub_time": "2026-01-01", "source": "B", "url": "https://c.test/"}]
    result = DataLoader().preprocess(original)
    assert len(result) == 1
    assert result[0]["sources"] == ["A", "B"]
    assert len(result[0]["source_urls"]) == 3
    assert "content_hash" not in original[0]


def test_v2_risk_zero_missing_and_contributions():
    from analyzer.risk_scorer import RiskScorer
    scorer = RiskScorer(weights={"conflict_frequency": .5, "nightlight_change": .5})
    assert scorer.calculate_risk_score({})["risk_score"] is None
    zero = scorer.calculate_risk_score({"conflict_frequency": 1, "nightlight_change": 0})
    assert zero["risk_score"] == 50
    missing = scorer.calculate_risk_score({"conflict_frequency": 1, "nightlight_change": None})
    assert missing["risk_score"] == 100
    assert missing["indicator_coverage"] == .5
    assert sum(x["contribution_points"] or 0 for x in zero["indicator_scores"].values()) == 50
    assert scorer.fill_missing([None, 0, None, 10, None]) == [None, 0, 5, 10, None]


def test_news_dedup_and_proxy_are_excluded():
    from analyzer.risk_scorer import RiskScorer
    scorer = RiskScorer()
    item = {"content": "conflict", "sentiment_score": .2}
    risk = scorer.compute_daily_risk([item, item], {"nightlight_change": -.5, "refugee_change": .9})
    assert risk["news_count"] == 1
    assert risk["raw_indicators"]["nightlight_change"] is None
    assert risk["raw_indicators"]["refugee_change"] is None


def test_forecast_minimum_coverage_backtest_and_missing_tail():
    from analyzer.trend import TrendAnalyzer
    analyzer = TrendAnalyzer()
    assert analyzer.forecast([20] * 13)["forecast"] == []
    assert analyzer.forecast([20] * 14 + [None] * 5)["forecast"] == []
    assert analyzer.forecast([20] * 30)["backtest"] is None
    result = analyzer.forecast([20 + i * .2 for i in range(70)])
    assert result["backtest"]["windows"] >= 4
    assert result["backtest"]["selected_model"] == "linear"
    assert result["interval"] is not None
    assert "R²" in result["confidence"]
    json.dumps(result, allow_nan=False)


def test_regression_real_dates_and_anomaly_no_lookahead():
    from analyzer.trend import TrendAnalyzer
    analyzer = TrendAnalyzer()
    dates = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(20) if i != 10]
    scores = [float((date.fromisoformat(d) - date(2026, 1, 1)).days) for d in dates]
    assert analyzer.linear_regression(scores, dates)["slope"] == pytest.approx(1)
    assert analyzer.detect_anomalies([10] * 13 + [100]) == []
    anomalies = analyzer.detect_anomalies([10] * 20 + [100])
    assert anomalies[-1]["index"] == 20
