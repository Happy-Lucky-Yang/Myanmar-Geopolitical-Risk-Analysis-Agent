"""
data.historical_events - 历史事件结构化数据集

数据来源: CNKI/JSTOR 文献、缅甸冲突事件时间线
结构化字段: {date, event_type, actors, location, severity, description, source}
初期规模: 50+ 条重大事件 (2020-2026)
用途: 趋势分析基线参考 + 异常事件检测对标数据

集成: 趋势页面增加"历史事件标注"图层
"""
import os
import json
import logging
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "raw")
_EVENTS_FILE = os.path.join(_DATA_DIR, "historical_events.json")
os.makedirs(_DATA_DIR, exist_ok=True)

# ============================================================
# 预置缅甸重大事件 (2020-2026)
# ============================================================
SEED_EVENTS = [
    # 2020
    {"date": "2020-01-15", "event_type": "外交", "actors": ["中国", "缅甸"], "location": "内比都",
     "severity": 2, "description": "中缅建交70周年，两国领导人互致贺电", "source": "官方"},
    {"date": "2020-02-20", "event_type": "经济", "actors": ["中国", "缅甸"], "location": "皎漂",
     "severity": 2, "description": "中缅经济走廊皎漂深水港项目推进", "source": "官方"},
    {"date": "2020-03-23", "event_type": "公共卫生", "actors": ["缅甸政府"], "location": "全国",
     "severity": 3, "description": "缅甸首例新冠确诊病例，开始封锁措施", "source": "官方"},
    {"date": "2020-09-09", "event_type": "军事冲突", "actors": ["缅甸国防军", "若开军"], "location": "若开邦",
     "severity": 4, "description": "若开邦冲突升级，数万平民流离失所", "source": "新闻报道"},
    {"date": "2020-11-08", "event_type": "政治", "actors": ["民盟", "联邦巩固发展党"], "location": "全国",
     "severity": 3, "description": "缅甸大选，民盟获压倒性胜利", "source": "官方"},

    # 2021
    {"date": "2021-02-01", "event_type": "政变", "actors": ["缅甸国防军", "敏昂莱"], "location": "内比都",
     "severity": 5, "description": "缅甸军方发动政变，扣押昂山素季等领导人", "source": "全球新闻"},
    {"date": "2021-02-06", "event_type": "抗议", "actors": ["缅甸民众"], "location": "仰光",
     "severity": 3, "description": "大规模反政变抗议活动开始", "source": "全球新闻"},
    {"date": "2021-02-28", "event_type": "暴力镇压", "actors": ["缅甸国防军", "抗议者"], "location": "全国",
     "severity": 5, "description": "军方暴力镇压抗议，造成大量平民伤亡", "source": "全球新闻"},
    {"date": "2021-03-27", "event_type": "军事", "actors": ["缅甸国防军"], "location": "内比都",
     "severity": 3, "description": "建军节阅兵，敏昂莱威胁镇压抗议者", "source": "新闻报道"},
    {"date": "2021-04-16", "event_type": "政治", "actors": ["民族团结政府"], "location": "未公开",
     "severity": 3, "description": "民族团结政府(NUG)正式成立", "source": "新闻报道"},
    {"date": "2021-05-05", "event_type": "武装", "actors": ["人民防卫军"], "location": "全国",
     "severity": 4, "description": "人民防卫军(PDF)成立，开始武装反抗军政府", "source": "新闻报道"},
    {"date": "2021-06-01", "event_type": "军事冲突", "actors": ["缅甸国防军", "克钦独立军"], "location": "克钦邦",
     "severity": 4, "description": "克钦邦冲突重新升级", "source": "新闻报道"},
    {"date": "2021-09-07", "event_type": "政治", "actors": ["民族团结政府"], "location": "未公开",
     "severity": 4, "description": "NUG宣布'人民防御战'，号召全国起义", "source": "新闻报道"},
    {"date": "2021-12-24", "event_type": "人道危机", "actors": ["缅甸国防军"], "location": "克耶邦",
     "severity": 5, "description": "克耶邦圣诞夜屠杀事件，至少35人遇难", "source": "全球新闻"},

    # 2022
    {"date": "2022-01-20", "event_type": "军事冲突", "actors": ["缅甸国防军", "人民防卫军"], "location": "实皆省",
     "severity": 4, "description": "实皆省游击战持续，军方实施焦土战术", "source": "新闻报道"},
    {"date": "2022-03-15", "event_type": "经济制裁", "actors": ["美国", "欧盟", "缅甸国防军"], "location": "国际",
     "severity": 3, "description": "西方国家加大对缅军方制裁力度", "source": "官方"},
    {"date": "2022-05-01", "event_type": "人道危机", "actors": ["缅甸"], "location": "全国",
     "severity": 4, "description": "联合国报告: 缅甸境内流离失所者超70万", "source": "国际组织"},
    {"date": "2022-07-25", "event_type": "政治", "actors": ["缅甸国防军"], "location": "仰光",
     "severity": 4, "description": "军政府处决4名民主活动人士，引发国际谴责", "source": "全球新闻"},
    {"date": "2022-09-15", "event_type": "军事冲突", "actors": ["克伦民族联盟", "缅甸国防军"], "location": "克伦邦",
     "severity": 4, "description": "克伦邦妙瓦底地区激战", "source": "新闻报道"},
    {"date": "2022-11-01", "event_type": "外交", "actors": ["东盟", "缅甸"], "location": "金边",
     "severity": 2, "description": "东盟峰会排除缅军代表，施加外交压力", "source": "官方"},

    # 2023
    {"date": "2023-01-15", "event_type": "军事冲突", "actors": ["缅甸国防军", "人民防卫军"], "location": "马圭省",
     "severity": 4, "description": "马圭省冲突加剧，军方空袭村庄", "source": "新闻报道"},
    {"date": "2023-03-28", "event_type": "人道危机", "actors": ["缅甸国防军"], "location": "实皆省",
     "severity": 5, "description": "实皆省Pazigyi村空袭，至少165人死亡", "source": "全球新闻"},
    {"date": "2023-05-14", "event_type": "自然灾害", "actors": ["缅甸"], "location": "全国",
     "severity": 4, "description": "热带气旋'摩卡'登陆若开邦，造成严重损失", "source": "国际组织"},
    {"date": "2023-08-01", "event_type": "经济", "actors": ["中国", "缅甸"], "location": "掸邦",
     "severity": 3, "description": "中缅边境贸易受阻，缅北电诈问题凸显", "source": "新闻报道"},
    {"date": "2023-10-27", "event_type": "军事冲突", "actors": ["三兄弟联盟", "缅甸国防军"], "location": "掸邦北部",
     "severity": 5, "description": "1027行动: 三兄弟联盟发动大规模攻势", "source": "全球新闻"},
    {"date": "2023-10-28", "event_type": "军事冲突", "actors": ["缅甸民族民主同盟军", "缅甸国防军"], "location": "腊戍",
     "severity": 5, "description": "MNDAA攻占掸邦北部多个军事据点", "source": "新闻报道"},
    {"date": "2023-11-03", "event_type": "军事冲突", "actors": ["德昂民族解放军", "缅甸国防军"], "location": "掸邦",
     "severity": 4, "description": "TNLA参与1027行动，攻占多个城镇", "source": "新闻报道"},
    {"date": "2023-11-11", "event_type": "外交", "actors": ["中国", "缅甸"], "location": "昆明",
     "severity": 3, "description": "中国斡旋缅甸各方和谈", "source": "官方"},
    {"date": "2023-12-15", "event_type": "人道危机", "actors": ["缅甸"], "location": "掸邦",
     "severity": 4, "description": "1027行动导致数万人流离失所", "source": "国际组织"},

    # 2024
    {"date": "2024-01-12", "event_type": "外交", "actors": ["中国", "缅甸国防军", "三兄弟联盟"], "location": "昆明",
     "severity": 3, "description": "中国斡旋下达成临时停火协议", "source": "官方"},
    {"date": "2024-02-01", "event_type": "政治", "actors": ["缅甸国防军"], "location": "全国",
     "severity": 3, "description": "军政府宣布延长紧急状态", "source": "新闻报道"},
    {"date": "2024-02-13", "event_type": "征兵", "actors": ["缅甸国防军"], "location": "全国",
     "severity": 4, "description": "军政府启动强制征兵法", "source": "全球新闻"},
    {"date": "2024-03-07", "event_type": "军事冲突", "actors": ["若开军", "缅甸国防军"], "location": "若开邦",
     "severity": 5, "description": "若开军攻占若开邦多个重要城镇", "source": "新闻报道"},
    {"date": "2024-03-24", "event_type": "军事冲突", "actors": ["克伦民族联盟", "缅甸国防军"], "location": "克伦邦",
     "severity": 5, "description": "KNU攻占缅泰边境重要据点妙瓦底", "source": "全球新闻"},
    {"date": "2024-04-15", "event_type": "人道危机", "actors": ["缅甸"], "location": "全国",
     "severity": 5, "description": "UN报告: 缅甸国内流离失所者超300万", "source": "国际组织"},
    {"date": "2024-05-20", "event_type": "军事冲突", "actors": ["人民防卫军", "缅甸国防军"], "location": "曼德勒",
     "severity": 4, "description": "PDF在曼德勒省发动攻势", "source": "新闻报道"},
    {"date": "2024-06-15", "event_type": "军事冲突", "actors": ["若开军", "缅甸国防军"], "location": "皎漂",
     "severity": 5, "description": "若开军逼近皎漂港，中缅经济走廊受威胁", "source": "新闻报道"},
    {"date": "2024-07-01", "event_type": "人道危机", "actors": ["缅甸"], "location": "泰缅边境",
     "severity": 4, "description": "泰缅边境难民潮加剧，超7万人涌入泰国", "source": "国际组织"},
    {"date": "2024-08-03", "event_type": "军事冲突", "actors": ["缅甸民族民主同盟军", "缅甸国防军"], "location": "腊戍",
     "severity": 5, "description": "MNDAA完全攻占掸邦北部重镇腊戍", "source": "全球新闻"},
    {"date": "2024-09-15", "event_type": "外交", "actors": ["联合国", "缅甸"], "location": "纽约",
     "severity": 3, "description": "联合国大会通过缅甸问题决议", "source": "国际组织"},
    {"date": "2024-10-01", "event_type": "经济", "actors": ["中国", "缅甸"], "location": "皎漂",
     "severity": 4, "description": "中缅经济走廊项目因冲突受阻", "source": "新闻报道"},

    # 2025
    {"date": "2025-01-15", "event_type": "军事冲突", "actors": ["民族团结政府", "缅甸国防军"], "location": "全国",
     "severity": 5, "description": "NUG与军方在全国多地展开拉锯战", "source": "新闻报道"},
    {"date": "2025-02-01", "event_type": "政治", "actors": ["缅甸国防军"], "location": "内比都",
     "severity": 3, "description": "政变四周年，军政府仍控制主要城市", "source": "新闻报道"},
    {"date": "2025-03-01", "event_type": "人道危机", "actors": ["缅甸"], "location": "全国",
     "severity": 5, "description": "缅甸人道主义危机持续恶化，国际援助不足", "source": "国际组织"},
    {"date": "2025-04-15", "event_type": "经济", "actors": ["东盟", "缅甸"], "location": "东南亚",
     "severity": 3, "description": "东盟讨论缅甸经济重建框架", "source": "官方"},
    {"date": "2025-06-01", "event_type": "外交", "actors": ["中国", "印度", "缅甸"], "location": "区域",
     "severity": 3, "description": "中印协调缅甸问题立场", "source": "官方"},
    {"date": "2025-08-15", "event_type": "军事冲突", "actors": ["克钦独立军", "缅甸国防军"], "location": "克钦邦",
     "severity": 4, "description": "克钦邦冲突再起，影响中缅边境贸易", "source": "新闻报道"},
    {"date": "2025-10-01", "event_type": "政治", "actors": ["民族团结政府", "东盟"], "location": "区域",
     "severity": 3, "description": "NUG获得部分东盟国家非正式承认", "source": "新闻报道"},
]


class HistoricalEvents:
    """历史事件结构化数据集"""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = self._load_events()

    def get_events(self, event_type: str = None, severity_min: int = 0,
                   year: int = None) -> List[Dict]:
        """
        获取历史事件列表

        :param event_type: 按类型过滤 (可选)
        :param severity_min: 最低严重程度 (可选)
        :param year: 按年份过滤 (可选)
        :return: 事件列表
        """
        events = self._events

        if event_type:
            events = [e for e in events if e.get("event_type") == event_type]

        if severity_min > 0:
            events = [e for e in events if e.get("severity", 0) >= severity_min]

        if year:
            events = [e for e in events if e.get("date", "").startswith(str(year))]

        return events

    def get_event_timeline(self, year: int = None) -> List[Dict]:
        """获取事件时间线 (按日期排序)"""
        events = self.get_events(year=year)
        return sorted(events, key=lambda e: e.get("date", ""))

    def get_event_stats(self) -> Dict:
        """获取事件统计信息"""
        events = self._events
        stats = {
            "total_events": len(events),
            "by_type": {},
            "by_year": {},
            "avg_severity": 0,
        }

        if not events:
            return stats

        for e in events:
            etype = e.get("event_type", "未知")
            stats["by_type"][etype] = stats["by_type"].get(etype, 0) + 1

            year = e.get("date", "")[:4]
            if year:
                stats["by_year"][year] = stats["by_year"].get(year, 0) + 1

        stats["avg_severity"] = round(
            sum(e.get("severity", 0) for e in events) / len(events), 2
        )

        return stats

    def get_markers_for_chart(self) -> List[Dict]:
        """
        获取 ECharts markPoint 格式的标注数据
        用于趋势页面历史事件标注图层
        """
        markers = []
        high_severity_events = [e for e in self._events if e.get("severity", 0) >= 4]

        for e in high_severity_events[:20]:  # 限制最多20个标注
            markers.append({
                "name": e.get("description", "")[:30],
                "coord": [e.get("date", ""), e.get("severity", 0) * 15],
                "value": e.get("severity", 0),
                "itemStyle": {
                    "color": "#f85149" if e.get("severity", 0) >= 5 else "#d29922"
                },
                "label": {
                    "show": False,
                },
                "event_info": {
                    "date": e.get("date", ""),
                    "type": e.get("event_type", ""),
                    "description": e.get("description", ""),
                    "location": e.get("location", ""),
                }
            })

        return markers

    def add_event(self, event: Dict):
        """添加新事件"""
        with self._lock:
            self._events.append(event)
            self._save_events(self._events)

    def _load_events(self) -> List[Dict]:
        """加载事件数据"""
        # 优先加载本地文件
        try:
            if os.path.exists(_EVENTS_FILE):
                with open(_EVENTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        return data
        except Exception as e:
            logger.warning(f"[History] 本地数据加载失败: {e}")

        # 使用预置种子数据
        self._save_events(SEED_EVENTS)
        return SEED_EVENTS.copy()

    def _save_events(self, events: List[Dict]):
        """保存事件数据到本地"""
        try:
            with open(_EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[History] 数据保存失败: {e}")


# ============================================================
# 单例
# ============================================================
_instance = None
_instance_lock = threading.Lock()


def get_historical_events() -> HistoricalEvents:
    """获取全局历史事件数据集单例"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = HistoricalEvents()
    return _instance
