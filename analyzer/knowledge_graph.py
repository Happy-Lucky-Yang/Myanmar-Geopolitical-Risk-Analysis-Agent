"""
analyzer.knowledge_graph - Neo4j 知识图谱模块（可选）
负责将 NER 提取的实体和关系存入 Neo4j 图数据库，支持查询与可视化
"""
import logging
import threading
from typing import Dict, List, Optional
from utils.config import get_neo4j_config
from utils.data_contract import (FORMAL_MODES, article_identity, business_date, fingerprint,
                                 source_evidence, time_window, json_safe)

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """Neo4j 知识图谱操作类"""

    ENTITY_TYPES = frozenset({'Country', 'Organization', 'Person', 'Event', 'Location', 'NewsEvent', 'EventType'})
    EVIDENCE_TYPES = frozenset({'MENTIONS_LOCATION', 'MENTIONS_ORGANIZATION', 'MENTIONS_PERSON',
                                'CLASSIFIED_AS', 'CO_OCCURS_IN_ARTICLE'})
    DEMO_TYPES = frozenset({'CONFLICT_WITH', 'COOPERATE_WITH', 'AFFILIATED_WITH', 'MEMBER_OF',
        'ECONOMIC_PARTNER', 'INVESTS_IN', 'SANCTIONS', 'MONITORS', 'DIPLOMATIC_PRESSURE',
        'REFUGEE_HOST', 'BORDER_RELATION', 'AID_PROVIDER', 'LEADS', 'BASED_IN', 'SYMBOLIC_LEADER',
        'PERPETRATED', 'TRIGGERED', 'CAUSES', 'OPERATES_IN', 'AFFECTS', 'TRADE_PARTNER',
        'LOCATED_IN', 'INVOLVED_IN'})

    @classmethod
    def entity_id(cls, name, entity_type, domain='legacy', article_id=None):
        if entity_type not in cls.ENTITY_TYPES or domain not in {'observed', 'legacy', 'demo'}:
            raise ValueError('无效实体类型或数据域')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('实体名不能为空')
        return fingerprint([domain, entity_type, article_id or ' '.join(name.split()).casefold()])

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
            logger.info("[KnowledgeGraph] Neo4j 未启用，跳过连接")
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
            logger.info(f"[KnowledgeGraph] Neo4j 连接成功: {self._uri}")
        except ImportError:
            logger.warning("[KnowledgeGraph] neo4j 驱动未安装，请运行: pip install neo4j")
            self._enabled = False
        except Exception as e:
            logger.error(f"[KnowledgeGraph] Neo4j 连接失败: {e}")
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

        props = dict(properties or {})
        domain = props.get('data_domain', 'legacy')
        identity = self.entity_id(name, entity_type, domain, props.get('article_id'))
        props.update(id=identity, name=name, data_domain=domain)
        query = f'MERGE (n:Entity:{entity_type} {{id: $id}}) SET n += $props'
        with self._driver.session() as session:
            session.run(query, id=identity, props=json_safe(props)).consume()
        return identity

    def add_relationship(self, source: str, target: str,
                          relation_type: str, properties: Dict = None, *, source_id=None, target_id=None):
        """
        添加实体间关系

        :param source: 源实体名称
        :param target: 目标实体名称
        :param relation_type: 关系类型（CONFLICT_WITH/COOPERATE_WITH/LOCATED_IN/INVOLVED_IN）
        :param properties: 关系属性
        """
        if not self._enabled:
            return

        props = dict(properties or {})
        domain = props.get('data_domain', 'legacy')
        if domain not in {'observed', 'demo', 'legacy'} or relation_type not in self.EVIDENCE_TYPES | self.DEMO_TYPES:
            raise ValueError('无效关系类型或数据域')
        if domain == 'observed':
            if (relation_type not in self.EVIDENCE_TYPES or not props.get('evidence_id')
                    or not business_date(props.get('date')) or not props.get('sources')
                    or not source_id or not target_id):
                raise ValueError('正式关系需要明确实体ID、日期、来源与证据，不自动推断合作/冲突/因果')
            props['date'] = business_date(props['date']).isoformat()
        identity = fingerprint([source_id or source, target_id or target, relation_type, domain, props.get('evidence_id')])
        props.update(id=identity, data_domain=domain)
        query = f'''MATCH (a:Entity), (b:Entity)
            WHERE a.data_domain = $domain AND b.data_domain = $domain
              AND (($source_id IS NOT NULL AND a.id = $source_id) OR ($source_id IS NULL AND a.name = $source))
              AND (($target_id IS NOT NULL AND b.id = $target_id) OR ($target_id IS NULL AND b.name = $target))
            MERGE (a)-[r:{relation_type} {{id: $id}}]->(b) SET r += $props'''
        with self._driver.session() as session:
            session.run(query, source=source, target=target, source_id=source_id, target_id=target_id,
                        domain=domain, id=identity, props=json_safe(props)).consume()
        return identity

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

        if news_item.get('run_kind') not in FORMAL_MODES:
            return 0
        day = business_date(news_item.get('published_at') or news_item.get('pub_time') or news_item.get('date'))
        evidence = source_evidence(news_item)
        if day is None or not evidence or not isinstance(entities, dict):
            return 0
        aid = article_identity(news_item)
        title = news_item.get('title') or aid
        props = {'article_id': aid, 'evidence_id': aid, 'date': day.isoformat(),
                 'data_domain': 'observed', 'run_kind': news_item['run_kind'],
                 'sources': sorted({entry['source'] for entry in evidence}),
                 'source_evidence': __import__('json').dumps(evidence, ensure_ascii=False),
                 'method': 'article_entity_mention', 'algorithm_version': news_item.get('ner_version', 'unknown')}
        news_id = self.add_entity(title, 'NewsEvent', props)
        count = 0
        for field, kind, relationship in (('locations', 'Location', 'MENTIONS_LOCATION'),
                ('organizations', 'Organization', 'MENTIONS_ORGANIZATION'), ('persons', 'Person', 'MENTIONS_PERSON')):
            values = entities.get(field, [])
            if not isinstance(values, list):
                continue
            for name in sorted({v.strip() for v in values if isinstance(v, str) and v.strip()}):
                entity_id = self.add_entity(name, kind, {'data_domain': 'observed'})
                self.add_relationship(title, name, relationship, props, source_id=news_id, target_id=entity_id)
                count += 1
        if (isinstance(llm_result, dict) and llm_result.get('analysis_status') == 'ok'
                and isinstance(llm_result.get('event_type'), str) and llm_result['event_type'] != '未知'):
            name = llm_result['event_type']
            entity_id = self.add_entity(name, 'EventType', {'data_domain': 'observed'})
            self.add_relationship(title, name, 'CLASSIFIED_AS', {**props, 'method': 'llm_classification'},
                                  source_id=news_id, target_id=entity_id)
            count += 1
        return count

    def query_entities(self, entity_name: str, **filters) -> List[Dict]:
        """
        查询实体及其直接关联

        :param entity_name: 实体名称
        :return: 关联实体列表
        """
        if not self._enabled:
            return []

        return self.get_graph_data_for_vis(center_entity=entity_name, **filters)['edges']

    def get_graph_data_for_vis(self, center_entity: str = None,
                                 max_nodes: int = 30, days=30, end_date=None, source=None, domain='observed') -> Dict:
        """
        获取知识图谱数据用于前端可视化

        :param center_entity: 中心实体（可选）
        :param max_nodes: 最大节点数
        :return: {"nodes": [...], "edges": [...]}
        """
        if not self._enabled:
            return {"nodes": [], "edges": []}

        if domain not in {'observed', 'demo', 'legacy'} or not isinstance(max_nodes, int) or not 1 <= max_nodes <= 500:
            raise ValueError('无效图谱数据域或节点上限（1～500）')
        start, end = time_window(days, end_date)
        query = '''MATCH (n:Entity)-[r]->(m:Entity)
            WHERE n.data_domain = $domain AND m.data_domain = $domain AND r.data_domain = $domain
              AND ($domain <> 'observed' OR (r.date >= $start AND r.date < $end AND r.evidence_id IS NOT NULL))
              AND ($center IS NULL OR n.name = $center OR m.name = $center)
              AND ($source IS NULL OR $source IN r.sources)
            RETURN n, r, m ORDER BY r.id LIMIT $max'''
        params = {'domain': domain, 'start': start.isoformat(), 'end': end.isoformat(),
                  'source': source, 'center': center_entity, 'max': max_nodes * 10}

        try:
            with self._driver.session() as session:
                result = session.run(query, **params)
                nodes, edges = {}, []
                for record in result:
                    left, right = dict(record['n']), dict(record['m'])
                    if len(nodes.keys() | {left['id'], right['id']}) > max_nodes:
                        continue
                    nodes[left['id']], nodes[right['id']] = left, right
                    edges.append({**dict(record['r']), 'source': left['id'], 'target': right['id'],
                                  'type': record['r'].type})
                return {'nodes': list(nodes.values()), 'edges': edges, 'data_domain': domain,
                        'limit': max_nodes, 'warning': '关系表示报道证据或显式演示，不代表已验证因果'}
        except Exception as e:
            logger.error('[KnowledgeGraph] 获取可视化数据失败')
            raise RuntimeError('图谱查询失败，未使用种子回填') from e

    def close(self):
        """关闭 Neo4j 连接"""
        if self._driver:
            self._driver.close()
            logger.info("[KnowledgeGraph] Neo4j 连接已关闭")


# 模块级单例
_kg_instance = None
_kg_lock = threading.Lock()


def get_knowledge_graph() -> KnowledgeGraph:
    """获取全局知识图谱单例（线程安全）"""
    global _kg_instance
    if _kg_instance is None:
        with _kg_lock:
            if _kg_instance is None:
                _kg_instance = KnowledgeGraph()
    return _kg_instance
