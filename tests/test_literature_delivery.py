"""文献交付处理（2026-11-16）：合并去重、私密字段不泄漏、主题映射、难民待审隔离。"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from analyzer import knowledge_base as kb
from analyzer.literature_catalog import public_record, PUBLIC_FIELDS

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'process_literature_delivery.py'
spec = importlib.util.spec_from_file_location('lit_delivery', SCRIPT)
lit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lit)


def base_rec(**over):
    rec = {'id': 'lit-0001', 'seq': 1, 'category': '01 缅甸研究文献', 'subcategory': '',
           'title': '缅甸稀土出口与中国供应链', 'title_zh': '缅甸稀土出口与中国供应链',
           'authors': ['张三'], 'institutions': [], 'year': 2024, 'journal': '资源科学',
           'abstract': '', 'keywords': [], 'doi': '', 'source_db': 'CNKI',
           'relevance': '相关', 'relevance_rank': 3, 'core_status': '否', 'is_core': False,
           'topics': ['缅甸·资源能源与农业'], 'modules': [], 'indicators': ['nightlight_change'],
           'reason': '', 'notes': '', 'kb_file': ''}
    rec.update(over)
    return rec


def test_merge_delivery_dedupes_and_fills_only_empty(tmp_path):
    index = kb.build_index([base_rec()], source_name='总表.xlsx')
    extra = tmp_path / 'extra.jsonl'
    dup = base_rec(id='lit-9001', seq=9001, title='缅甸稀土出口与中国供应链！')
    new = base_rec(id='lit-9002', seq=9002, title='缅甸内战与边境安全', abstract='摘要文本',
                   relevance='需人工核查', relevance_rank=1, core_status='需人工核查')
    extra.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in (dup, new)), encoding='utf-8')
    hold = tmp_path / 'hold.json'
    hold.write_text(json.dumps({'lit-0001': {'holdings': [{'sha256': 'a' * 64, 'path': '私密/x.pdf'}],
                                           'fill': {'abstract': '补摘要', 'doi': '10.1234/x'}}}), encoding='utf-8')
    merged = kb.merge_delivery(index, str(extra), str(hold))
    assert merged['record_count'] == 2 and merged['delivery_added'] == 1
    rec = next(r for r in merged['records'] if r['id'] == 'lit-0001')
    assert rec['abstract'] == '补摘要' and rec['doi'] == '10.1234/x'
    assert rec['local_holdings'][0]['path'] == '私密/x.pdf'
    assert merged['facets']['relevance'].get('需人工核查') == 1


def test_public_record_never_leaks_private_fields():
    rec = base_rec(kb_file='私密/x.pdf', holding_sha256='b' * 64,
                   local_holdings=[{'path': '私密/x.pdf'}], notes='内部备注')
    pub = public_record(rec)
    assert set(pub) <= set(PUBLIC_FIELDS) | {'doi', 'links', 'fulltext_available'}
    assert '私密' not in json.dumps(pub, ensure_ascii=False)
    assert '内部备注' not in json.dumps(pub, ensure_ascii=False)


def test_topic_rules_map_to_modules_and_indicators():
    topics = lit.assign_topics('缅甸军方与克钦独立军在掸邦爆发武装冲突，难民越境')
    assert '缅甸·军方与安全事务' in topics and '缅甸·社会人道与治理' in topics
    mods = {kb.TOPIC_TO_MODULE[t] for t in topics if t in kb.TOPIC_TO_MODULE}
    inds = {i for t in topics for i in kb.TOPIC_TO_INDICATORS.get(t, [])}
    assert 'conflict_frequency' in inds and 'displacement' in inds
    assert lit.assign_topics('完全无关的烹饪书') == ['其他']


def test_extract_meta_cnki_and_anna_filenames():
    row = {'path': '01/缅甸稀土出口与中国供应链_张三.pdf', 'text': '', 'zip': 'z'}
    meta = lit.extract_meta(row)
    assert meta['title'] == '缅甸稀土出口与中国供应链' and meta['authors'] == ['张三']
    anna = {'path': 'x/Myanmar in 2023 -- Brenner, David -- Asian Survey, 64 -- doi 10_1525_as_2024_64_2_330 -- abc -- Anna.pdf',
            'text': '', 'zip': 'z'}
    meta2 = lit.extract_meta(anna)
    assert meta2['doi'] == '10.1525/as.2024.64.2.330'
    assert meta2['title'].startswith('Myanmar in 2023')


def test_refugee_staging_idempotent_and_eligible_zero(tmp_path):
    db = tmp_path / 'review.sqlite3'
    cands = [{'asset_id': 'refugee-' + 'c' * 16, 'path': 'IDMC/a.csv', 'sha256': 'c' * 64,
              'location': 'IDMC/a.csv', 'payload': {'rows': 21}},
             {'asset_id': 'refugee-' + 'd' * 16, 'path': 'UNHCR/b.csv', 'sha256': 'd' * 64,
              'location': 'UNHCR/b.csv', 'payload': {'rows': 41}}]
    assert lit.stage_refugee_rows(db, cands, apply=True) == 2
    assert lit.stage_refugee_rows(db, cands, apply=True) == 0
    conn = sqlite3.connect(db)
    rows = conn.execute('select kind, status, eligible_for_scoring from pending_records').fetchall()
    assert len(rows) == 2
    assert all(k == 'displacement_candidate' and s == 'pending_review' and e == 0 for k, s, e in rows)
    # 预演不写库
    db2 = tmp_path / 'dry.sqlite3'
    assert lit.stage_refugee_rows(db2, cands, apply=False) == 2
    assert sqlite3.connect(db2).execute('select count(*) from pending_records').fetchone()[0] == 0
