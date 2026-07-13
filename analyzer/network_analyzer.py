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

    def analyze(self, graph_data: Dict = None) -> Dict:
        """
        执行完整的关系网络分析

        :param graph_data: {"nodes": [...], "edges": [...]} 格式,
                          若为 None 则从 Neo4j 或本地文件获取
        :return: 分析结果字典
        """
        nx = self._get_nx()

        # 构建图
        G = self._build_graph(nx, graph_data)

        if G.number_of_nodes() == 0:
            return {
                "node_count": 0,
                "edge_count": 0,
                "density": 0,
                "top_actors": [],
                "communities": [],
                "error": "无节点数据"
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
                "name": name,
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
        """构建 NetworkX 图"""
        G = nx.DiGraph()

        if graph_data:
            # 从传入数据构建
            for node in graph_data.get("nodes", []):
                name = node.get("name", node.get("id", ""))
                if name:
                    G.add_node(name, **{k: v for k, v in node.items() if k != "name"})

            for edge in graph_data.get("edges", []):
                src = edge.get("source", "")
                tgt = edge.get("target", "")
                if src and tgt:
                    G.add_edge(src, tgt, **{k: v for k, v in edge.items()
                                           if k not in ("source", "target")})
            return G

        # 尝试从 Neo4j 获取
        try:
            from analyzer.knowledge_graph import get_knowledge_graph
            kg = get_knowledge_graph()
            if kg._enabled:
                data = kg.get_graph_data_for_vis(max_nodes=100)
                for node in data.get("nodes", []):
                    G.add_node(node["name"])
                for edge in data.get("edges", []):
                    G.add_edge(edge["source"], edge["target"], type=edge.get("type", ""))
                if G.number_of_nodes() > 0:
                    return G
        except Exception as e:
            logger.debug(f"[Network] Neo4j 不可用: {e}")

        # 降级: 从本地 JSON 或种子数据构建
        try:
            if os.path.exists(_LOCAL_RELATIONS_FILE):
                with open(_LOCAL_RELATIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for node in data.get("nodes", []):
                    G.add_node(node.get("name", ""), type=node.get("type", ""))
                for edge in data.get("edges", []):
                    G.add_edge(edge["source"], edge["target"], type=edge.get("type", ""))
                return G
        except Exception:
            pass

        # 最终降级: 从 kg_seeder 预置数据构建
        self._build_from_seed_data(G)
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
                members = sorted(list(community_set))[:8]  # 限制每社区显示数
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
                            "members": sorted(list(component))[:8],
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
