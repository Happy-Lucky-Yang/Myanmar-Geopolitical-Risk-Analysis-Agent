# -*- coding: utf-8 -*-
"""事件密度（KDE）分析器单元测试（合成数据，无网络依赖）"""
from datetime import datetime, timedelta
from itertools import count

_EVENT_IDS = count()

import pytest

from analyzer.event_density import EventDensityAnalyzer, LAT_RANGE, LON_RANGE


def _mk_event(lat, lon, root="19", quad="4", date=None, num_sources=2):
    """构造合成 GDELT 事件（默认高烈度冲突、双信源互证）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    return {
        "event_id": str(next(_EVENT_IDS)),
        "date": date,
        "event_code": root + "2",
        "root_code": root,
        "quad_class": quad,
        "num_sources": num_sources,
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
        events.append(_mk_event(16.88 + (i % 5) * 0.01, 96.12 + (i % 4) * 0.01))
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
    events.append(_mk_event(16.8, 96.25))
    assert analyzer.compute(events, days=7)["located_count"] == 31


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


def test_z_matrix_and_bbox_for_raster(analyzer):
    """栅格渲染输入：z 矩阵形状正确、境外为 0、bbox 完整"""
    result = analyzer.compute(_cluster_events(), days=7)

    z = result["z_matrix"]
    assert z.shape == (60, 48)  # fixture 网格尺寸
    assert 0.99 <= z.max() <= 1.0
    assert result["bbox"] == [[9.4, 91.8], [28.6, 101.3]]


def test_verified_events_counted(analyzer):
    """互证事件（num_sources>=2）被计数"""
    events = _cluster_events()  # 默认 num_sources=2
    result = analyzer.compute(events, days=7)
    assert result["verified_count"] == result["located_count"]


def test_single_source_reprints_are_not_independent_evidence(analyzer):
    """同一来源多篇报道不能充当多个独立信源。"""
    events = []
    for i in range(10):
        ev = _mk_event(16.8 + (i % 3) * 0.05, 96.15 + (i % 2) * 0.06,
                       num_sources=1)
        ev["num_articles"] = 5
        events.append(ev)
    result = analyzer.compute(events, days=7)
    assert result["verified_count"] == 0
    assert result["reported_multi_source_count"] == 0


def test_min_sources_filter():
    """信源门槛：单信源事件在 min_sources=2 时被排除"""
    strict = EventDensityAnalyzer(
        {"grid_rows": 30, "grid_cols": 24, "min_events": 2,
         "min_sources": 2})
    # 4 条单信源 + 4 条双信源（坐标带二维抖动，避免共线奇异）
    events = [_mk_event(16.8 + i * 0.05, 96.2 + (i % 2) * 0.06,
                        num_sources=1)
              for i in range(4)]
    events += [_mk_event(21.4 + i * 0.05, 97.9 + (i % 2) * 0.06,
                         num_sources=3)
               for i in range(4)]
    result = strict.compute(events, days=7)
    assert result["event_count"] == 8
    assert result["located_count"] == 4  # 仅双/多信源参与
    assert result["verified_count"] == 4


def test_render_density_png(analyzer):
    """matplotlib 栅格 PNG 渲染输出合法 data URI"""
    from visualization.map_gen import render_density_png

    result = analyzer.compute(_cluster_events(), days=7)
    uri = render_density_png(result["z_matrix"], result["bbox"])
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) > 1000  # 非空图像
