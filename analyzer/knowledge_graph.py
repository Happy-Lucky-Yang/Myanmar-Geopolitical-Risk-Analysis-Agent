"""
analyzer.knowledge_graph - Neo4j 知识图谱模块（可选）
负责将 NER 提取的实体和关系存入 Neo4j 图数据库，支持查询与可视化
"""
from typing import Dict, List, Optional
from utils.config import get_neo4j_config


class KnowledgeGraph:
    """Neo4j 知识图谱操作类"""

    def __init__(self):
        cfg = get_neo4j_config()
        self._enabled = cfg.get("enabled", False)
        self._uri = cfg.get("uri", "bolt://localhost:7687")
        self._user = cfg.get("user", "neo4j")
        self._password = cfg.get("password", "")
        self._driver = None

        if self._enabled:
            self._connect()

    def _connect(self):
        """连接 Neo4j 数据库"""
        if not self._enabled:
            print("[KnowledgeGraph] Neo4j 未启用，跳过连接")
            return

        try:
            # TODO: pip install neo4j
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password)
            )
            # 测试连接
            with self._driver.session() as session:
                session.run("RETURN 1")
            print(f"[KnowledgeGraph] Neo4j 连接成功: {self._uri}")
        except ImportError:
            print("[KnowledgeGraph] neo4j 驱动未安装，请运行: pip install neo4j")
            self._enabled = False
        except Exception as e:
            print(f"[KnowledgeGraph] Neo4j 连接失败: {e}")
            self._enabled = False

    def add_entity(self, name: str, entity_type: str, properties: Dict = None):
        """
        添加实体节点

        :param name: 实体名称
        :param entity_type: 实体类型（Country/Organization/Person/Event/Location）
        :param properties: 附加属性
        """
        if not self._enabled:
            return

        props = properties or {}
        props["name"] = name

        # 构建属性字符串
        prop_str = ", ".join(f'{k}: "${k}"' for k in props)

        query = f"""
        MERGE (n:{entity_type} {{name: $name}})
        SET n += {{{prop_str}}}
        """

        try:
            with self._driver.session() as session:
                session.run(query, **props)
        except Exception as e:
            print(f"[KnowledgeGraph] 添加实体失败: {e}")

    def add_relationship(self, source: str, target: str,
                          relation_type: str, properties: Dict = None):
        """
        添加实体间关系

        :param source: 源实体名称
        :param target: 目标实体名称
        :param relation_type: 关系类型（CONFLICT_WITH/COOPERATE_WITH/LOCATED_IN/INVOLVED_IN）
        :param properties: 关系属性
        """
        if not self._enabled:
            return

        props = properties or {}
        prop_str = ", ".join(f'{k}: "${k}"' for k in props) if props else ""
        prop_clause = f" SET r += {{{prop_str}}}" if prop_str else ""

        query = f"""
        MATCH (a {{name: $source}}), (b {{name: $target}})
        MERGE (a)-[r:{relation_type}]->(b)
        {prop_clause}
        """

        try:
            with self._driver.session() as session:
                session.run(query, source=source, target=target, **props)
        except Exception as e:
            print(f"[KnowledgeGraph] 添加关系失败: {e}")

    def add_news_analysis(self, news_item: Dict, entities: Dict,
                            llm_result: Dict = None):
        """
        将一条新闻的分析结果写入知识图谱

        :param news_item: 新闻条目 {"title": ..., "date": ..., "source": ...}
        :param entities: NER 提取的实体 {"locations": [], "organizations": [], ...}
        :param llm_result: 大模型分析结果（可选）
        """
        if not self._enabled:
            return

        # 1. 创建新闻节点
        self.add_entity(
            name=news_item.get("title", "Unknown"),
            entity_type="NewsEvent",
            properties={
                "date": news_item.get("date", ""),
                "source": news_item.get("source", ""),
            }
        )

        # 2. 创建实体节点并建立关系
        for loc in entities.get("locations", []):
            self.add_entity(loc, "Location")
            self.add_relationship(
                news_item.get("title", ""),
                loc, "MENTIONS_LOCATION"
            )

        for org in entities.get("organizations", []):
            self.add_entity(org, "Organization")
            self.add_relationship(
                news_item.get("title", ""),
                org, "MENTIONS_ORGANIZATION"
            )

        for person in entities.get("persons", []):
            self.add_entity(person, "Person")
            self.add_relationship(
                news_item.get("title", ""),
                person, "MENTIONS_PERSON"
            )

        # 3. 如果大模型识别了事件类型，添加事件节点
        if llm_result and llm_result.get("event_type"):
            event_type = llm_result["event_type"]
            self.add_entity(event_type, "EventType")
            self.add_relationship(
                news_item.get("title", ""),
                event_type, "CLASSIFIED_AS"
            )

    def query_entities(self, entity_name: str) -> List[Dict]:
        """
        查询实体及其直接关联

        :param entity_name: 实体名称
        :return: 关联实体列表
        """
        if not self._enabled:
            return []

        query = """
        MATCH (n {name: $name})-[r]-(m)
        RETURN n, type(r) as relation, m
        LIMIT 50
        """

        try:
            with self._driver.session() as session:
                result = session.run(query, name=entity_name)
                return [
                    {
                        "source": entity_name,
                        "relation": record["relation"],
                        "target": record["m"]["name"]
                    }
                    for record in result
                ]
        except Exception as e:
            print(f"[KnowledgeGraph] 查询失败: {e}")
            return []

    def get_graph_data_for_vis(self, center_entity: str = None,
                                 max_nodes: int = 30) -> Dict:
        """
        获取知识图谱数据用于前端可视化

        :param center_entity: 中心实体（可选）
        :param max_nodes: 最大节点数
        :return: {"nodes": [...], "edges": [...]}
        """
        if not self._enabled:
            return {"nodes": [], "edges": []}

        if center_entity:
            query = """
            MATCH (n {name: $center})-[r]-(m)
            WITH n, r, m LIMIT $max
            RETURN n, r, m
            """
            params = {"center": center_entity, "max": max_nodes}
        else:
            query = """
            MATCH (n)-[r]->(m)
            RETURN n, r, m
            LIMIT $max
            """
            params = {"max": max_nodes}

        try:
            with self._driver.session() as session:
                result = session.run(query, **params)
                nodes = set()
                edges = []

                for record in result:
                    n_name = record["n"]["name"]
                    m_name = record["m"]["name"]
                    nodes.add(n_name)
                    nodes.add(m_name)
                    edges.append({
                        "source": n_name,
                        "target": m_name,
                        "type": record["r"].type
                    })

                return {
                    "nodes": [{"name": name} for name in nodes],
                    "edges": edges
                }
        except Exception as e:
            print(f"[KnowledgeGraph] 获取可视化数据失败: {e}")
            return {"nodes": [], "edges": []}

    def close(self):
        """关闭 Neo4j 连接"""
        if self._driver:
            self._driver.close()
            print("[KnowledgeGraph] Neo4j 连接已关闭")


# 模块级单例
_kg_instance = None


def get_knowledge_graph() -> KnowledgeGraph:
    """获取全局知识图谱单例"""
    global _kg_instance
    if _kg_instance is None:
        _kg_instance = KnowledgeGraph()
    return _kg_instance
