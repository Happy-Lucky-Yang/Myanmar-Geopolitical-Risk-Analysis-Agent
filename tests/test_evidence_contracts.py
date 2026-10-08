"""事件计数、共现网络及Neo4j参数/证据域的离线回归。"""
from unittest.mock import MagicMock

import pytest


def article(**changes):
    return {'title': '报道', 'content': '同一正文', 'pub_time': '2026-01-01',
            'source': 'fixture', 'url': 'https://fixture.test/1', 'run_kind': 'existing',
            'analyzed_at': '2026-01-02T00:00:00Z', 'ner_version': 'fixture-v1',
            'entities': {'organizations': ['组织A', '组织B'], 'persons': ['组织A']}, **changes}


def test_event_metrics_missing_zero_unknown_and_duplicates():
    from data.gdelt_files import compute_metrics_from_events, event_severity_weight, event_category
    empty = compute_metrics_from_events([])
    assert empty['event_count'] == 0 and empty['article_count'] is None
    assert empty['conflict_frequency'] is empty['avg_tone_risk'] is empty['avg_severity'] is None
    first = {'event_id': '1', 'date': '20260101', 'root_code': 1, 'avg_tone': 0}
    unknown = {'event_id': '2', 'date': '20260101', 'root_code': None,
               'avg_tone': float('nan'), 'location': []}
    result = compute_metrics_from_events([first, first, unknown, 5, {'date': 'bad'},
                                          {**first, 'event_id': 'demo', 'run_kind': 'demo'}])
    assert result['event_count'] == 2 and result['classified_count'] == 1
    assert result['conflict_frequency'] == 0 and result['avg_tone_risk'] == .5
    assert result['tone_sample_count'] == 1
    assert event_severity_weight(unknown) is None and event_category(unknown) == 'unknown'


def test_gdelt_api_is_read_only_and_windowed(monkeypatch):
    import data.gdelt_crawler as crawler
    def forbidden(*args, **kwargs):
        raise AssertionError('GET不得触发采集')
    monkeypatch.setattr(crawler, 'get_gdelt_crawler', forbidden)
    from data.event_store import get_event_store
    from app import app
    client = app.test_client()
    assert client.get('/api/gdelt').json['data']['event_count'] == 0
    get_event_store().append([{'event_id': '1', 'date': '20260101', 'source': 'gdelt', 'root_code': '19'}])
    result = client.get('/api/gdelt?days=1&end_date=2026-01-01').json['data']
    assert result['event_count'] == 1 and result['article_count'] is None
    assert result['avg_tone_risk'] is None
    assert client.get('/api/gdelt?days=1&end_date=2026-01-02').json['data']['event_count'] == 0


def test_network_evidence_domains_types_and_frequency():
    from analyzer.network_analyzer import build_article_graph, NetworkAnalyzer
    rows = [article(), article(source='other', url='https://other.test/2'),
            article(content='另一报道'), article(run_kind='manual', content='私密'),
            article(run_kind='demo', content='演示'), article(pub_time='2020-01-01', content='旧闻')]
    graph = build_article_graph(rows, 1, '2026-01-01')
    assert len(graph['nodes']) == 3  # 同名人和组织不能合并
    assert len(graph['edges']) == 3
    edge = graph['edges'][0]
    assert edge['type'] == 'CO_OCCURS_IN_ARTICLE' and edge['weight'] == 2
    assert len(edge['evidence'][0]['sources']) + len(edge['evidence'][1]['sources']) == 3
    result = NetworkAnalyzer().analyze(graph)
    assert result['node_count'] == 3 and result['density'] == 1
    assert all(len(a['name']) < 64 for a in result['top_actors'])
    assert build_article_graph(rows, 1, '2026-01-01', source='absent')['nodes'] == []
    assert build_article_graph(rows, 1, '2026-01-01', region='MMR.1_1')['nodes'] == []
    assert NetworkAnalyzer().analyze(days=1, end_date='2026-01-01')['density'] is None
    assert NetworkAnalyzer().analyze(domain='demo')['metadata']['data_domain'] == 'demo'


def test_network_deduplicates_before_window_and_selects_latest_entities():
    from analyzer.network_analyzer import build_article_graph
    old = article(pub_time='2025-12-31', analyzed_at='2026-01-01T00:00:00Z')
    new = article(pub_time='2026-01-02', analyzed_at='2026-01-03T00:00:00Z',
                  entities={'organizations': ['组织A', '组织C']}, ner_version='fixture-v2')
    for rows in ([old, new], [new, old]):
        assert build_article_graph(rows, 2, '2026-01-02')['nodes'] == []
        graph = build_article_graph(rows, 3, '2026-01-02')
        assert {n['name'] for n in graph['nodes']} == {'组织A', '组织C'}
        assert graph['metadata']['ner_versions'] == ['fixture-v2']
        assert graph['edges'][0]['evidence'][0]['date'] == '2025-12-31'
    assert build_article_graph([old, new], 3, '2026-01-02') == build_article_graph([new, old], 3, '2026-01-02')


def test_network_source_filter_preserves_source_url_pairs():
    from analyzer.network_analyzer import build_article_graph
    rows = [article(source='A', url='https://a.test/1'), article(source='B', url='https://b.test/2')]
    graph = build_article_graph(rows, 1, '2026-01-01', source='B')
    assert graph['metadata']['article_count'] == 1
    assert graph['edges'][0]['evidence'][0]['sources'] == [{'source': 'B', 'url': 'https://b.test/2'}]


def test_network_api_uses_persisted_entities():
    from analyzer.data_loader import get_data_loader
    from app import app
    get_data_loader().save_articles([article()])
    data = app.test_client().get('/api/network?days=1&end_date=2026-01-01').json['data']
    assert data['node_count'] == 3 and data['revision']
    assert data['metadata']['sources'] == ['fixture']


def fake_graph():
    from analyzer.knowledge_graph import KnowledgeGraph
    graph = KnowledgeGraph.__new__(KnowledgeGraph)
    graph._enabled = True
    graph._driver = MagicMock()
    return graph, graph._driver.session.return_value.__enter__.return_value


def test_neo4j_parameters_and_label_validation():
    graph, session = fake_graph()
    props = {'date': '2026-01-01', 'data_domain': 'demo'}
    identity = graph.add_entity('名称', 'Person', props)
    query, = session.run.call_args.args
    params = session.run.call_args.kwargs
    assert 'SET n += $props' in query and params['props']['date'] == '2026-01-01'
    assert params['props']['name'] == '名称' and 'id' not in props
    assert identity != graph.entity_id('名称', 'Person', 'observed')
    count = session.run.call_count
    with pytest.raises(ValueError):
        graph.add_entity('名称', 'Person) DELETE n //', props)
    with pytest.raises(ValueError):
        graph.add_relationship('a', 'b', 'CAUSES', {'data_domain': 'observed'})
    with pytest.raises(ValueError):
        graph.add_relationship('a', 'b', 'BAD]->()', {'data_domain': 'demo'})
    assert session.run.call_count == count


def test_news_relations_keep_ids_sources_time_and_reject_manual():
    graph, session = fake_graph()
    assert graph.add_news_analysis(article(run_kind='manual'), {}) == 0
    assert session.run.call_count == 0
    assert graph.add_news_analysis(article(), article()['entities']) == 3
    queries = session.run.call_args_list
    evidence = [call.kwargs for call in queries if 'MERGE (a)-[r:' in call.args[0]]
    assert len(evidence) == 3
    assert all(e['props']['date'] == '2026-01-01' and e['props']['sources'] == ['fixture'] for e in evidence)
    assert all(e['source_id'] and e['target_id'] and e['props']['evidence_id'] for e in evidence)
    assert len({e['id'] for e in evidence}) == 3
    graph.get_graph_data_for_vis(days=1, end_date='2026-01-01', source='fixture')
    assert session.run.call_args.kwargs['domain'] == 'observed'
    assert session.run.call_args.kwargs['end'] == '2026-01-02'


def test_seeding_is_demo_only(monkeypatch):
    from data.kg_seeder import KGSeeder
    graph, session = fake_graph()
    seeder = KGSeeder()
    seeder._kg = graph
    monkeypatch.setattr(seeder, 'seed_from_news_data', lambda: pytest.fail('种子导入不扫描真实文章'))
    assert seeder.seed_all()['data_domain'] == 'demo'
    assert all(call.kwargs['props']['data_domain'] == 'demo' for call in session.run.call_args_list)
