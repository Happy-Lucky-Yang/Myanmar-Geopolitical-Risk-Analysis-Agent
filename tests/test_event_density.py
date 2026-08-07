# -*- coding: utf-8 -*-
"""事件密度（KDE）分析器单元测试（合成数据，无网络依赖）"""
from datetime import datetime, timedelta

import pytest

from analyzer.event_density import EventDensityAnalyzer, LAT_RANGE, LON_RANGE


def _mk_event(lat, lon, root="19", quad="4", date=None):
    """构造合成 GDELT 事件（默认高烈度冲突）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    return {
        "date": date,
        "event_code": root + "2",
        "root_code": root,
        "quad_class": quad,
        "avg_tone": -5.0,
        "location": "Test, Myanmar",
        "lat": lat,
        "lon": lon,
        "source_url": "http://example.com",
    }


@pytest.fixture
def analyzer():
    # 小网格加速测试
    return EventDensityAnalyzer({"grid_rows": 60, "grid_cols": 48, "min_events": 5})


def _cluster_events():
    """仰光周边 20 条冲突 + 掸邦 10 条冲突（全部境内有效坐标）"""
    events = []
    for i in range(20):
        events.append(_mk_event(16.8 + (i % 5) * 0.05, 96.15 + (i % 4) * 0.05))
    for i in range(10):
        events.append(_mk_event(21.4 + (i % 3) * 0.06, 97.9 + (i % 3) * 0.06))
    return events


def test_compute_returns_normalized_grid(analyzer):
    result = analyzer.compute(_cluster_events(), days=7)

    assert result["degraded"] is None
    assert result["event_count"] == 30
    assert result["located_count"] == 30
    assert len(result["grid"]) > 0

    weights = [pt[2] for pt in result["grid"]]
    assert max(weights) == pytest.approx(1.0)  # 归一化峰值为 1
    assert min(weights) >= 0.02  # 阈值去噪生效（舍入后允许等于 0.02）


def test_peak_within_bbox(analyzer):
    result = analyzer.compute(_cluster_events(), days=7)
    assert LAT_RANGE[0] <= result["peak_lat"] <= LAT_RANGE[1]
    assert LON_RANGE[0] <= result["peak_lon"] <= LON_RANGE[1]


def test_mask_excludes_outside_country(analyzer):
    """国界掩膜：所有非零网格点必须位于缅甸境内"""
    result = analyzer.compute(_cluster_events(), days=7)

    import numpy as np
    pts = np.array([[pt[1], pt[0]] for pt in result["grid"]])  # (lon, lat)
    inside = analyzer._inside_country(pts)
    assert inside.all(), "存在国境之外的非零密度点"


def test_invalid_coordinates_skipped(analyzer):
    events = _cluster_events()
    events.append(_mk_event(None, None))   # 无坐标
    events.append(_mk_event(16.9, 96.2))
    result = analyzer.compute(events, days=7)
    assert result["event_count"] == 32
    assert result["located_count"] == 31  # None 坐标被剔除


def test_too_few_events_degrades(analyzer):
    result = analyzer.compute(_cluster_events()[:3], days=7)
    assert result["degraded"] is not None
    assert "grid" not in result


def test_date_window_filter(analyzer):
    """days 过滤：30 天前的旧事件不计入 7 天窗口"""
    old_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    events = [_mk_event(16.85, 96.18, date=old_date) for _ in range(20)]
    events.extend(_cluster_events()[:6])

    result = analyzer.compute(events, days=7)
    assert result["event_count"] == 6  # 旧事件被过滤
    assert result["located_count"] == 6


def test_empty_events_degrades(analyzer):
    result = analyzer.compute([], days=7)
    assert result["degraded"] is not None
    assert result["event_count"] == 0
