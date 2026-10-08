"""
analyzer.network_analyzer - NetworkX 关系网络分析

功能:
  - 从 Neo4j 或本地 JSON 构建关系网络图
  - 计算中心性指标 (度中心性、介数中心性、接近中心性)
  - 识别关键行为体 (hub nodes) 和社区结构 (Louvain)
  - 输出: top_actors, communities, network_density

集成:
  /api/analyze 响应新增 network_analysis 字段
"""
import json
import logging
import os
import threading
from typing import Dict, List, Optional
from itertools import combinations
from utils.data_contract import (FORMAL_MODES, article_identity, business_date, fingerprint,
                                 source_evidence, time_window, timestamp_utc)


def build_article_graph(records, days=30, end_date=None, region='MMR', source=None):
    """从正式文章中已有实体构建无向共现；不在查询时调用NER或LLM。"""
    start, end = time_window(days, end_date)
    articles = {}
    for row in records:
        if not isinstance(row, dict) or row.get('run_kind') not in FORMAL_MODES:
            continue
        day = business_date(row.get('published_at') or row.get('pub_time') or row.get('date'))
        if day is None or not isinstance(row.get('entities'), dict):
            continue
        evidence = source_evidence(row)
        aid = article_identity(row)
        previous = articles.get(aid)
        selected = row
        if previous:
            evidence = source_evidence({'source_evidence': previous['evidence'] + evidence})
            old_stamp, new_stamp = timestamp_utc(previous['row'].get('analyzed_at')), timestamp_utc(row.get('analyzed_at'))
            if (old_stamp and (new_stamp is None or old_stamp > new_stamp)
                    or old_stamp == new_stamp and fingerprint(previous['row']) < fingerprint(row)):
                selected = previous['row']
            day = min(day, business_date(previous['day']))
        articles[aid] = {'row': selected, 'evidence': evidence, 'day': day.isoformat()}
    selected_articles = {}
    for aid, item in articles.items():
        if not start <= business_date(item['day']) < end:
            continue
        if region != 'MMR':
            from analyzer.spatial_analysis import locate_event
            if locate_event(item['row'])[0] != region:
                continue
        if source:
            item['evidence'] = [e for e in item['evidence'] if e['source'] == source]
        if item['evidence']:
            selected_articles[aid] = item
    articles = selected_articles
    nodes, edges = {}, {}
    unassessed, excessive = 0, 0
    for aid, article in sorted(articles.items()):
        row = article['row']
        mentions = {}
        for field, kind in (('locations', 'Location'), ('organizations', 'Organization'), ('persons', 'Person')):
            names = row['entities'].get(field, [])
            if not isinstance(names, list):
                continue
            for name in names:
                if not isinstance(name, str) or not name.strip():
                    continue
                name = ' '.join(name.split())
                nid = fingerprint([kind, name.casefold()])
                mentions[nid] = {'id': nid, 'name': name, 'entity_type': kind}
        if len(mentions) > 50:
            excessive += 1
            continue
        if not mentions:
            unassessed += 1
            continue
        nodes.update(mentions)
        for left, right in combinations(sorted(mentions), 2):
            edge = edges.setdefault((left, right), {'source': left, 'target': right,
                       'type': 'CO_OCCURS_IN_ARTICLE', 'evidence': []})
            edge['evidence'].append({'article_id': aid, 'date': article['day'],
                'sources': article['evidence'], 'method': 'article_entity_cooccurrence',
                'algorithm_version': row.get('ner_version', 'unknown'), 'run_kind': row['run_kind']})
    for edge in edges.values():
        edge['weight'] = len(edge['evidence'])
    return {'nodes': sorted(nodes.values(), key=lambda n: n['id']),
            'edges': [edges[key] for key in sorted(edges)],
            'metadata': {'algorithm_version': 'network-v2', 'data_domain': 'observed',
                'requested_start': start.isoformat(), 'end_exclusive': end.isoformat(),
                'region': region, 'source': source, 'article_count': len(articles),
                'empty_entity_articles': unassessed, 'excluded_excessive_entities': excessive,
                'ner_versions': sorted({a['row'].get('ner_version', 'unknown') for a in articles.values()}),
                'sources': sorted({e['source'] for a in articles.values() for e in a['evidence']}),
                'relation_semantics': '文章级实体共现，不表示合作、冲突或因果；中心性不代表现实影响力'}}

logger = logging.getLogger(__name__)

# 本地关系数据缓存 (Neo4j 不可用时的降级方案)
_LOCAL_RELATIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                     "data", "raw", "network_relations.json")


class NetworkAnalyzer:
    """关系网络分析器"""

    def __init__(self):
        self._nx = None

    def _get_nx(self):
        """延迟导入 networkx"""
        if self._nx is None:
            try:
                import networkx as nx
                self._nx = nx
            except ImportError:
                raise RuntimeError("networkx 未安装，请运行: pip install networkx")
        return self._nx

    def analyze(self, graph_data: Dict = None, days=30, end_date=None,
                region='MMR', source=None, domain='observed') -> Dict:
        """
        执行完整的关系网络分析

        :param graph_data: {"nodes": [...], "edges": [...]} 格式,
                          若为 None 则从 Neo4j 或本地文件获取
        :return: 分析结果字典
        """
        nx = self._get_nx()
        if domain not in {'observed', 'demo'}:
            raise ValueError('网络数据域须为 observed 或 demo')
        if graph_data is None and domain == 'observed':
            from analyzer.data_loader import get_data_loader
            graph_data = build_article_graph(get_data_loader().load_raw_news(), days, end_date, region, source)
        if domain == 'demo':
            from data.kg_seeder import SEED_ENTITIES, SEED_RELATIONSHIPS
            graph_data = {'nodes': [dict(n, id=n['name']) for n in SEED_ENTITIES],
                          'edges': SEED_RELATIONSHIPS,
                          'metadata': {'data_domain': 'demo', 'warning': '演示种子，非当前真实关系；不应用时间筛选'}}

        # 构建图
        G = self._build_graph(nx, graph_data)

        if G.number_of_nodes() == 0:
            return {
                "node_count": 0,
                "edge_count": 0,
                "density": None,
                "top_actors": [],
                "communities": [],
                "metadata": (graph_data or {}).get('metadata', {}),
                "graph": graph_data,
                "error": "窗口内没有可用实体证据，未使用演示数据回填"
            }

        # 1. 基本统计
        density = nx.density(G)
        components = nx.number_connected_components(G.to_undirected())

        # 2. 中心性分析
        degree_centrality = nx.degree_centrality(G)
        try:
            betweenness = nx.betweenness_centrality(G)
        except Exception:
            betweenness = {}
        try:
            closeness = nx.closeness_centrality(G)
        except Exception:
            closeness = {}

        # 3. 识别关键行为体 (Top 10 by degree)
        top_actors = sorted(degree_centrality.items(), key=lambda x: x[1], reverse=True)[:10]
        top_actors_list = [
            {
                "id": name,
                "name": G.nodes[name].get('name', name),
                "degree_centrality": round(score, 4),
                "betweenness": round(betweenness.get(name, 0), 4),
                "closeness": round(closeness.get(name, 0), 4),
                "role": self._infer_role(name, G)
            }
            for name, score in top_actors
        ]

        # 4. 社区检测 (Louvain / 备选 greedy modularity)
        communities = self._detect_communities(nx, G)

        # 5. 关系类型分布
        relation_types = {}
        for _, _, data in G.edges(data=True):
            rtype = data.get("type", "UNKNOWN")
            relation_types[rtype] = relation_types.get(rtype, 0) + 1

        return {
            "metadata": (graph_data or {}).get('metadata', {}),
            "graph": graph_data,
            "node_count": G.number_of_nodes(),
            "edge_count": G.number_of_edges(),
            "density": round(density, 4),
            "components": components,
            "top_actors": top_actors_list,
            "communities": communities,
            "relation_types": relation_types,
            "avg_degree": round(2 * G.number_of_edges() / max(G.number_of_nodes(), 1), 2),
        }

    def _build_graph(self, nx, graph_data: Dict = None):
        """仅消费本次显式证据快照；正式视图没有外部库或种子回退。"""
        G = nx.Graph()
        for node in (graph_data or {}).get('nodes', []):
            identity = node.get('id') or node.get('name')
            if identity:
                G.add_node(identity, **node)
        for edge in (graph_data or {}).get('edges', []):
            left, right = edge.get('source'), edge.get('target')
            if left in G and right in G and left != right:
                G.add_edge(left, right, **{k: v for k, v in edge.items() if k not in {'source', 'target'}})
        return G

    def _build_from_seed_data(self, G):
        """从预置种子数据构建图 (Neo4j 不可用时的降级方案)"""
        try:
            from data.kg_seeder import SEED_ENTITIES, SEED_RELATIONSHIPS

            for entity in SEED_ENTITIES:
                props = entity.get("properties", {}).copy()
                props["entity_type"] = entity["type"]  # 避免与 networkx 'type' 关键字冲突
                G.add_node(entity["name"], **props)

            for rel in SEED_RELATIONSHIPS:
                props = rel.get("properties", {}).copy()
                props["relation_type"] = rel["type"]  # 避免冲突
                G.add_edge(rel["source"], rel["target"], **props)

        except ImportError:
            logger.warning("[Network] kg_seeder 不可用")

    def _detect_communities(self, nx, G) -> List[Dict]:
        """社区检测"""
        communities = []

        try:
            # 尝试 Louvain (networkx >= 3.0 内置)
            undirected = G.to_undirected()
            if undirected.number_of_nodes() < 2:
                return communities

            partition = nx.community.louvain_communities(undirected, seed=42)

            for i, community_set in enumerate(partition):
                members = sorted(G.nodes[n].get('name', n) for n in community_set)[:8]  # 限制每社区显示数
                communities.append({
                    "id": i,
                    "size": len(community_set),
                    "members": members,
                })

            # 按规模排序
            communities.sort(key=lambda c: c["size"], reverse=True)

        except (AttributeError, Exception) as e:
            logger.debug(f"[Network] 社区检测失败: {e}")
            # 降级: 基于连通分量
            try:
                for i, component in enumerate(nx.connected_components(G.to_undirected())):
                    if len(component) >= 2:
                        communities.append({
                            "id": i,
                            "size": len(component),
                            "members": sorted(G.nodes[n].get('name', n) for n in component)[:8],
                        })
            except Exception:
                pass

        return communities[:5]  # 最多返回5个社区

    def _infer_role(self, name: str, G) -> str:
        """根据节点属性推断角色描述"""
        node_data = G.nodes.get(name, {})
        ntype = node_data.get("entity_type", node_data.get("type", ""))

        role_map = {
            "Country": "国家",
            "Organization": "组织",
            "Person": "人物",
            "Location": "地点",
            "EventType": "事件类型",
            "NewsEvent": "新闻事件",
        }
        return role_map.get(ntype, ntype or "行为体")


# ============================================================
# 单例
# ============================================================
_instance = None
_lock = threading.Lock()


def get_network_analyzer() -> NetworkAnalyzer:
    """获取全局网络分析器单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = NetworkAnalyzer()
    return _instance
