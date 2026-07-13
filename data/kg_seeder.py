"""
data.kg_seeder - 知识图谱种子数据填充

功能:
  - 从已有 NER 结果批量导入实体节点 (国家/组织/人物/地点)
  - 从 GDELT 事件数据创建关系边 (冲突/合作/外交)
  - 目标: 至少 30 个节点 + 50 条关系 (中期成果要求)
  - 预置缅甸地缘政治核心实体与关系

使用:
  python -m data.kg_seeder              # 填充预置数据
  python -m data.kg_seeder --from-news  # 从已有新闻分析结果填充
"""
import os
import sys
import json
import logging
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# ============================================================
# 预置缅甸地缘政治核心实体
# ============================================================
SEED_ENTITIES = [
    # 国家
    {"name": "缅甸", "type": "Country", "properties": {"region": "东南亚", "status": "focus"}},
    {"name": "中国", "type": "Country", "properties": {"region": "东亚", "relation_to_myanmar": "邻国/投资方"}},
    {"name": "印度", "type": "Country", "properties": {"region": "南亚", "relation_to_myanmar": "邻国"}},
    {"name": "泰国", "type": "Country", "properties": {"region": "东南亚", "relation_to_myanmar": "邻国/难民接收"}},
    {"name": "美国", "type": "Country", "properties": {"region": "北美", "relation_to_myanmar": "制裁方"}},
    {"name": "日本", "type": "Country", "properties": {"region": "东亚", "relation_to_myanmar": "援助方"}},
    {"name": "东盟", "type": "Organization", "properties": {"type": "区域组织"}},
    {"name": "联合国", "type": "Organization", "properties": {"type": "国际组织"}},

    # 缅甸内部组织
    {"name": "缅甸国防军", "type": "Organization", "aliases": ["缅军", "Tatmadaw"]},
    {"name": "民族团结政府", "type": "Organization", "aliases": ["NUG"]},
    {"name": "人民防卫军", "type": "Organization", "aliases": ["PDF"]},
    {"name": "克钦独立军", "type": "Organization", "aliases": ["KIA"]},
    {"name": "掸邦军", "type": "Organization", "aliases": ["SSA"]},
    {"name": "克伦民族联盟", "type": "Organization", "aliases": ["KNU"]},
    {"name": "若开军", "type": "Organization", "aliases": ["AA"]},
    {"name": "德昂民族解放军", "type": "Organization", "aliases": ["TNLA"]},
    {"name": "缅甸民族民主同盟军", "type": "Organization", "aliases": ["MNDAA"]},
    {"name": "三兄弟联盟", "type": "Organization", "aliases": ["Three Brotherhood Alliance"]},

    # 关键人物
    {"name": "敏昂莱", "type": "Person", "properties": {"role": "军政府领导人"}},
    {"name": "昂山素季", "type": "Person", "properties": {"role": "民主派领袖"}},

    # 关键地点
    {"name": "仰光", "type": "Location", "properties": {"type": "最大城市"}},
    {"name": "内比都", "type": "Location", "properties": {"type": "首都"}},
    {"name": "掸邦", "type": "Location", "properties": {"type": "冲突热点"}},
    {"name": "克钦邦", "type": "Location", "properties": {"type": "冲突热点"}},
    {"name": "若开邦", "type": "Location", "properties": {"type": "冲突热点/罗兴亚"}},
    {"name": "腊戍", "type": "Location", "properties": {"type": "掸邦北部重镇"}},
    {"name": "皎漂港", "type": "Location", "properties": {"type": "中缅经济走廊项目"}},
    {"name": "中缅油气管道", "type": "Location", "properties": {"type": "战略基础设施"}},

    # 事件类型
    {"name": "军事冲突", "type": "EventType"},
    {"name": "政变", "type": "EventType"},
    {"name": "人道主义危机", "type": "EventType"},
    {"name": "经济制裁", "type": "EventType"},
    {"name": "难民危机", "type": "EventType"},
    {"name": "停火谈判", "type": "EventType"},
]

# ============================================================
# 预置核心关系
# ============================================================
SEED_RELATIONSHIPS = [
    # 冲突关系
    {"source": "缅甸国防军", "target": "人民防卫军", "type": "CONFLICT_WITH", "properties": {"since": "2021"}},
    {"source": "缅甸国防军", "target": "克钦独立军", "type": "CONFLICT_WITH", "properties": {"since": "2011"}},
    {"source": "缅甸国防军", "target": "掸邦军", "type": "CONFLICT_WITH", "properties": {"since": "2015"}},
    {"source": "缅甸国防军", "target": "克伦民族联盟", "type": "CONFLICT_WITH", "properties": {"since": "1949"}},
    {"source": "缅甸国防军", "target": "若开军", "type": "CONFLICT_WITH", "properties": {"since": "2019"}},
    {"source": "三兄弟联盟", "target": "缅甸国防军", "type": "CONFLICT_WITH", "properties": {"operation": "1027行动"}},
    {"source": "缅甸民族民主同盟军", "target": "缅甸国防军", "type": "CONFLICT_WITH", "properties": {"region": "掸邦北部"}},
    {"source": "德昂民族解放军", "target": "缅甸国防军", "type": "CONFLICT_WITH"},

    # 同盟关系
    {"source": "人民防卫军", "target": "民族团结政府", "type": "AFFILIATED_WITH"},
    {"source": "克钦独立军", "target": "三兄弟联盟", "type": "COOPERATE_WITH"},
    {"source": "缅甸民族民主同盟军", "target": "三兄弟联盟", "type": "MEMBER_OF"},
    {"source": "德昂民族解放军", "target": "三兄弟联盟", "type": "MEMBER_OF"},
    {"source": "若开军", "target": "三兄弟联盟", "type": "COOPERATE_WITH"},

    # 国际关系
    {"source": "中国", "target": "缅甸", "type": "ECONOMIC_PARTNER", "properties": {"type": "投资/贸易"}},
    {"source": "中国", "target": "皎漂港", "type": "INVESTS_IN"},
    {"source": "中国", "target": "中缅油气管道", "type": "INVESTS_IN"},
    {"source": "美国", "target": "缅甸", "type": "SANCTIONS", "properties": {"since": "2021"}},
    {"source": "美国", "target": "缅甸国防军", "type": "SANCTIONS"},
    {"source": "联合国", "target": "缅甸", "type": "MONITORS"},
    {"source": "东盟", "target": "缅甸", "type": "DIPLOMATIC_PRESSURE"},
    {"source": "泰国", "target": "缅甸", "type": "REFUGEE_HOST", "properties": {"refugees": "~90000"}},
    {"source": "印度", "target": "缅甸", "type": "BORDER_RELATION"},
    {"source": "日本", "target": "缅甸", "type": "AID_PROVIDER"},

    # 地理关系
    {"source": "敏昂莱", "target": "缅甸国防军", "type": "LEADS"},
    {"source": "敏昂莱", "target": "内比都", "type": "BASED_IN"},
    {"source": "昂山素季", "target": "民族团结政府", "type": "SYMBOLIC_LEADER"},
    {"source": "缅甸国防军", "target": "政变", "type": "PERPETRATED", "properties": {"date": "2021-02-01"}},
    {"source": "政变", "target": "军事冲突", "type": "TRIGGERED"},
    {"source": "军事冲突", "target": "难民危机", "type": "CAUSES"},
    {"source": "军事冲突", "target": "人道主义危机", "type": "CAUSES"},

    # 地点-组织关系
    {"source": "克钦独立军", "target": "克钦邦", "type": "OPERATES_IN"},
    {"source": "若开军", "target": "若开邦", "type": "OPERATES_IN"},
    {"source": "缅甸民族民主同盟军", "target": "掸邦", "type": "OPERATES_IN"},
    {"source": "缅甸民族民主同盟军", "target": "腊戍", "type": "OPERATES_IN"},

    # 经济关系
    {"source": "经济制裁", "target": "缅甸", "type": "AFFECTS"},
    {"source": "中国", "target": "缅甸", "type": "TRADE_PARTNER", "properties": {"rank": "最大贸易伙伴"}},
]


class KGSeeder:
    """知识图谱种子数据填充器"""

    def __init__(self):
        self._kg = None

    def _get_kg(self):
        if self._kg is None:
            from analyzer.knowledge_graph import get_knowledge_graph
            self._kg = get_knowledge_graph()
        return self._kg

    def seed_entities(self) -> int:
        """导入预置实体节点, 返回导入数量"""
        kg = self._get_kg()
        if not kg._enabled:
            logger.warning("[KGSeeder] Neo4j 未启用, 跳过种子数据")
            return 0

        count = 0
        for entity in SEED_ENTITIES:
            props = entity.get("properties", {}).copy()
            if "aliases" in entity:
                props["aliases"] = ",".join(entity["aliases"])
            kg.add_entity(entity["name"], entity["type"], props)
            count += 1

        logger.info(f"[KGSeeder] 导入 {count} 个实体节点")
        return count

    def seed_relationships(self) -> int:
        """导入预置关系边, 返回导入数量"""
        kg = self._get_kg()
        if not kg._enabled:
            logger.warning("[KGSeeder] Neo4j 未启用, 跳过关系数据")
            return 0

        count = 0
        for rel in SEED_RELATIONSHIPS:
            kg.add_relationship(
                rel["source"], rel["target"],
                rel["type"], rel.get("properties")
            )
            count += 1

        logger.info(f"[KGSeeder] 导入 {count} 条关系边")
        return count

    def seed_from_news_data(self, news_dir: str = None) -> int:
        """从已有新闻分析结果中提取实体和关系填充 KG"""
        if news_dir is None:
            news_dir = os.path.join(os.path.dirname(__file__), "raw")

        kg = self._get_kg()
        if not kg._enabled:
            return 0

        count = 0
        # 扫描 JSONL 文件
        for fname in os.listdir(news_dir):
            if not fname.endswith(".jsonl"):
                continue
            filepath = os.path.join(news_dir, fname)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # 提取实体
                        entities = record.get("entities", {})
                        title = record.get("title", "")[:100]
                        date = record.get("date", "")

                        if not title:
                            continue

                        # 创建新闻事件节点
                        kg.add_entity(title, "NewsEvent", {"date": date})

                        for loc in entities.get("locations", []):
                            kg.add_entity(loc, "Location")
                            kg.add_relationship(title, loc, "MENTIONS_LOCATION")
                            count += 1

                        for org in entities.get("organizations", []):
                            kg.add_entity(org, "Organization")
                            kg.add_relationship(title, org, "MENTIONS_ORGANIZATION")
                            count += 1

                        for person in entities.get("persons", []):
                            kg.add_entity(person, "Person")
                            kg.add_relationship(title, person, "MENTIONS_PERSON")
                            count += 1

            except Exception as e:
                logger.warning(f"[KGSeeder] 处理 {fname} 失败: {e}")

        logger.info(f"[KGSeeder] 从新闻数据导入 {count} 条关系")
        return count

    def seed_all(self) -> Dict:
        """执行全部种子数据填充"""
        entities = self.seed_entities()
        relationships = self.seed_relationships()
        news_count = self.seed_from_news_data()

        result = {
            "entities": entities,
            "relationships": relationships,
            "news_derived": news_count,
            "total": entities + relationships + news_count,
        }
        logger.info(f"[KGSeeder] 种子数据完成: {result}")
        return result


def main():
    """独立运行入口"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=" * 60)
    print("  知识图谱种子数据填充")
    print("=" * 60)

    seeder = KGSeeder()
    result = seeder.seed_all()

    print(f"\n填充结果:")
    print(f"  实体节点: {result['entities']}")
    print(f"  关系边:   {result['relationships']}")
    print(f"  新闻衍生: {result['news_derived']}")
    print(f"  总计:     {result['total']}")


if __name__ == "__main__":
    main()
