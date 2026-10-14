"""行为体关系网络（analyzer.network_analyzer.build_actor_network）契约测试。

覆盖组长《四模块实施说明》模块三：固定八节点、有向、Goldstein×NumMentions 加权净值、
合作/冲突分层，四条验收断言（8节点 / 边权∈[-10,10] / 无自环 / 模块度∈[-0.5,1]），
以及两处职责边界（不进风险合成、与实体网络不复用节点表）。
"""
from analyzer.network_analyzer import build_actor_network


def ev(a1, a2, g, m=1):
    return {'source': 'gdelt', 'event_id': f'{a1}-{a2}-{g}', 'date': '20260101',
            'actor1_country': a1, 'actor2_country': a2, 'goldstein': g, 'num_mentions': m}


def test_eight_nodes_and_codes():
    net = build_actor_network([], include_centrality=False)
    assert net['node_count'] == 8                                    # 断言(1)
    assert {n['id'] for n in net['nodes']} == {'MMR', 'CHN', 'USA', 'EUU', 'THA', 'IND', 'BGD', 'LAO'}
    eu = next(n for n in net['nodes'] if n['id'] == 'EUU')
    assert eu['code'] == 'EU' and eu['name'] == '欧盟'                # 欧盟作单一集体行为体


def test_no_self_loop_directed_and_range():
    events = [ev('BM', 'BM', -10.0, 5),        # 自环 → 排除
              ev('CH', 'BM', 6.0, 3),          # CHN→MMR 合作
              ev('US', 'BM', -8.0, 2)]         # USA→MMR 冲突
    net = build_actor_network(events, include_centrality=False)
    by = {(e['source'], e['target']): e for e in net['edges']}
    for e in net['edges']:
        assert e['source'] != e['target']                            # 断言(3)
        assert -10.0 <= e['weight'] <= 10.0                          # 断言(2)
    assert ('MMR', 'MMR') not in by                                  # 自环不计
    assert by[('CHN', 'MMR')]['weight'] == 6.0 and by[('CHN', 'MMR')]['layer'] == 'cooperation'
    assert by[('USA', 'MMR')]['weight'] == -8.0 and by[('USA', 'MMR')]['layer'] == 'conflict'


def test_weighted_net_by_mentions():
    # CHN→MMR：(6×3 + (−2)×1)/(3+1) = 16/4 = 4.0
    net = build_actor_network([ev('CH', 'BM', 6.0, 3), ev('CH', 'BM', -2.0, 1)], include_centrality=False)
    e = next(x for x in net['edges'] if (x['source'], x['target']) == ('CHN', 'MMR'))
    assert round(e['weight'], 6) == 4.0 and e['mentions'] == 4 and e['event_count'] == 2


def test_layers_split_by_sign():
    net = build_actor_network([ev('CH', 'BM', 6.0), ev('US', 'BM', -8.0), ev('TH', 'BM', -3.0)],
                              include_centrality=False)
    coop, conf = net['layers']['cooperation'], net['layers']['conflict']
    assert all(e['weight'] > 0 for e in coop) and all(e['weight'] < 0 for e in conf)
    assert len(coop) == 1 and len(conf) == 2


def test_boundaries_documented():
    net = build_actor_network([], include_centrality=False)
    assert net['metadata']['enters_risk_synthesis'] is False         # 职责边界(1)
    assert net['metadata']['directed'] is True
    assert 'build_article_graph' in net['metadata']['boundary']      # 职责边界(2)


def test_centrality_modularity_in_range():
    """断言(4)：networkx 可用且某层有边时，模块度须落在 [-0.5, 1]。"""
    events = [ev('CH', 'BM', 6.0), ev('BM', 'CH', 5.0), ev('US', 'BM', -8.0),
              ev('BM', 'US', -7.0), ev('TH', 'BM', -3.0), ev('EU', 'BM', -4.0)]
    cen = build_actor_network(events, include_centrality=True)['centrality']
    if cen['status'] != 'ok':
        return                                                       # networkx 不可用则跳过
    for layer in ('cooperation', 'conflict'):
        mod = cen['layers'][layer]['modularity']
        assert mod is None or -0.5 <= mod <= 1.0                     # 断言(4)
    assert 'mmr_betweenness' in cen
