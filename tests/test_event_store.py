# -*- coding: utf-8 -*-
"""事件累积库单元测试（临时文件，无网络依赖）"""
import os
import json
from datetime import datetime, timedelta

import pytest

from data.event_store import EventStore


@pytest.fixture
def store_path(tmp_path):
    return str(tmp_path / "store.json")


def _ev(eid, date, lat=16.8, lon=96.2):
    return {
        "event_id": eid,
        "date": date,
        "event_code": "192",
        "root_code": "19",
        "quad_class": "4",
        "num_sources": 2,
        "avg_tone": -5.0,
        "location": "Yangon",
        "lat": lat,
        "lon": lon,
        "source_url": f"http://x/{eid}",
    }


def test_append_dedupes_by_event_id(store_path):
    store = EventStore(persist_path=store_path)
    today = datetime.now().strftime("%Y%m%d")

    added1 = store.append([_ev("1", today), _ev("2", today)])
    added2 = store.append([_ev("2", today), _ev("3", today)])  # 2 重复

    assert added1 == 2
    assert added2 == 1
    assert store.stats()["total"] == 3


def test_append_fallback_fingerprint_without_id(store_path):
    """无 event_id 的事件按 日期+事件码+位置 指纹去重"""
    store = EventStore(persist_path=store_path)
    today = datetime.now().strftime("%Y%m%d")

    ev = _ev("", today)
    ev.pop("event_id")
    added1 = store.append([dict(ev)])
    added2 = store.append([dict(ev)])  # 完全相同 → 指纹重复

    assert added1 == 1
    assert added2 == 0


def test_load_filters_by_days(store_path):
    store = EventStore(persist_path=store_path)
    today = datetime.now()
    recent = today.strftime("%Y%m%d")
    old = (today - timedelta(days=20)).strftime("%Y%m%d")

    store.append([_ev("1", recent), _ev("2", old)])

    assert len(store.load(days=7)) == 1
    assert len(store.load(days=30)) == 2
    assert len(store.load()) == 2  # 无窗口=全部


def test_history_retained_and_window_filters_old_events(store_path):
    store = EventStore(persist_path=store_path, keep_days=30)
    today = datetime.now()
    store.append([
        _ev("1", today.strftime("%Y%m%d")),
        _ev("2", (today - timedelta(days=60)).strftime("%Y%m%d")),
    ])
    # 保留原始历史，查询窗口不能改变持久化资料。
    assert store.stats()["total"] == 2
    assert len(store.load(days=30)) == 1
    assert EventStore(persist_path=store_path).stats()["total"] == 2


def test_persistence_roundtrip(store_path):
    store = EventStore(persist_path=store_path)
    today = datetime.now().strftime("%Y%m%d")
    store.append([_ev("1", today), _ev("2", today)])

    # 新实例从磁盘加载
    store2 = EventStore(persist_path=store_path)
    assert store2.stats()["total"] == 2
    # 去重索引同步恢复，重复追加不新增
    assert store2.append([_ev("1", today)]) == 0


def test_corrupted_file_degrades_gracefully(store_path):
    with open(store_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    store = EventStore(persist_path=store_path)
    assert store.stats()["total"] == 0  # 损坏文件不崩溃，空库启动
