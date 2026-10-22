"""
analyzer.ner - 命名实体识别模块
支持中文（LAC）和英文（spaCy）文本的实体提取
提取地名、组织名、人名等实体，输出统一格式
"""
import re
import logging
import threading
from typing import Dict, List

logger = logging.getLogger(__name__)

# 中文 NER：百度 LAC（首选，需 paddlepaddle）
try:
    from LAC import LAC
except Exception as exc:
    LAC = None
    logger.warning('[NER] 可选LAC加载失败: %s', type(exc).__name__)

# 中文 NER 回退：jieba 词性标注（纯 Python，全版本可用）
try:
    import jieba
    import jieba.posseg as pseg
except ImportError:
    jieba = None
    pseg = None

# 英文 NER：spaCy
try:
    import spacy
except ImportError:
    spacy = None

# 英文地名词典（gazetteer）：spaCy en_core_web_sm 对缅甸地名存在系统性误判，
# 实测把 Naypyitaw / Rakhine / Shan State 全部标为 PERSON，导致关系网络把首都显示成"人物"。
# 这里只收录通用地理常识（省邦、主要城市、邻国），命中即强制归为地点。
EN_GEO_GAZETTEER = {
    # 缅甸省邦
    'naypyitaw', 'nay pyi taw', 'yangon', 'mandalay', 'rakhine', 'arakans', 'arakan',
    'shan', 'kachin', 'karen', 'kayin', 'chin', 'mon', 'kayah', 'sagaing', 'magway',
    'bago', 'tanintharyi', 'ayeyarwady', 'irrawaddy',
    # 主要城市与口岸
    'sittwe', 'kyaukphyu', 'mrauk-u', 'muse', 'myitkyina', 'lashio', 'pathein',
    'hpa-an', 'loikaw', 'dawei', 'myeik', 'bagan', 'meiktila', 'taunggyi',
    'mawlamyine', 'monywa', 'pyay', 'mogok', 'hkamti', 'paletwa', 'buthidaung',
    'maungdaw', 'laukkai', 'kokang', 'wa state',
    # 国家与区域
    'myanmar', 'burma', 'thailand', 'china', 'india', 'bangladesh', 'laos',
    'malaysia', 'indonesia', 'singapore', 'vietnam', 'cambodia', 'philippines',
    'japan', 'south korea', 'russia', 'australia', 'united states', 'united kingdom',
    'france', 'germany', 'asean', 'yunnan', 'ruili', 'bangkok', 'beijing',
    'new delhi', 'dhaka', 'washington', 'jakarta', 'manila', 'hanoi', 'vientiane',
    'phnom penh', 'kuala lumpur', 'tokyo', 'seoul', 'moscow', 'london', 'paris',
    'berlin', 'geneva', 'new york', 'brussels', 'mekong', 'salween', 'chindwin',
    'gulf of bengal', 'indian ocean', 'andaman sea',
}
# 行政区后缀："Shan State"、"Rakhine Region" 剥离后缀后再查词典
EN_GEO_SUFFIXES = (' state', ' region', ' division', ' province', ' township', ' city', ' district')


def is_known_en_location(name) -> bool:
    """实体名是否为已知地名（含行政区后缀变体），用于纠正 spaCy 的 PERSON 误判。"""
    if not isinstance(name, str):
        return False
    key = ' '.join(name.lower().split()).strip(' .,')
    if not key:
        return False
    if key in EN_GEO_GAZETTEER:
        return True
    for suffix in EN_GEO_SUFFIXES:
        if key.endswith(suffix):
            stem = key[:-len(suffix)].strip()
            if stem in EN_GEO_GAZETTEER:
                return True
    return False

# 缅甸地缘政治词典（jieba 自定义词，提升实体识别召回）
# (词, 词性)  ns=地名 nr=人名 nt=机构
JIEBA_GEO_DICT = [
    # 地名/行政区
    ("缅甸", "ns"), ("仰光", "ns"), ("内比都", "ns"), ("掸邦", "ns"), ("克钦邦", "ns"),
    ("克伦邦", "ns"), ("若开邦", "ns"), ("钦邦", "ns"), ("克耶邦", "ns"), ("孟邦", "ns"),
    ("实皆省", "ns"), ("马圭省", "ns"), ("勃固省", "ns"), ("伊洛瓦底省", "ns"),
    ("德林达依省", "ns"), ("曼德勒", "ns"), ("腊戌", "ns"), ("木姐", "ns"), ("勐拉", "ns"),
    # 人名
    ("昂山素季", "nr"), ("敏昂莱", "nr"), ("登盛", "nr"), ("吴廷觉", "nr"),
    # 机构/组织/武装
    ("缅甸国防军", "nt"), ("若开军", "nt"), ("克钦独立军", "nt"), ("德昂民族解放军", "nt"),
    ("缅甸民族民主同盟军", "nt"), ("民族团结政府", "nt"), ("国家管理委员会", "nt"),
    ("东盟", "nt"), ("联合国", "nt"), ("欧盟", "nt"), ("世界银行", "nt"),
    ("三兄弟联盟", "nt"), ("佤邦联合军", "nt"),
    # —— 通用领域地名表（gazetteer）：邻国 / 主要城市 / 边境口岸 ——
    # 仅收录任何缅甸地缘系统都应具备的通用地理常识，不含特定报道的复合标签
    # （带斜杠/括号的机构全称、文章专属人名不纳入，避免过拟合与子串误报）
    ("中国", "ns"), ("泰国", "ns"), ("印度", "ns"), ("孟加拉国", "ns"),
    ("老挝", "ns"), ("越南", "ns"), ("柬埔寨", "ns"),
    ("妙瓦底", "ns"), ("毛淡棉", "ns"), ("蒲甘", "ns"), ("密支那", "ns"),
    ("皎漂", "ns"), ("大其力", "ns"), ("帕敢", "ns"), ("果敢", "ns"),
    ("南坎", "ns"), ("贵概", "ns"), ("瑞丽", "ns"),
    # —— 通用机构 / 政治组织 ——
    ("军政府", "nt"), ("联邦议会", "nt"), ("缅甸中央银行", "nt"),
    ("国际货币基金组织", "nt"),
    # —— 通用人名（历史与国家领导人，避免与更长人名构成子串冲突）——
    ("温敏", "nr"), ("奈温", "nr"), ("丹瑞", "nr"), ("吴努", "nr"),
    ("敏瑞", "nr"), ("索温", "nr"),
]
_jieba_dict_loaded = False


def _ensure_jieba_dict():
    """向 jieba 注入缅甸地缘词典（仅一次）"""
    global _jieba_dict_loaded
    if _jieba_dict_loaded or jieba is None:
        return
    for word, flag in JIEBA_GEO_DICT:
        jieba.add_word(word, freq=100000, tag=flag)
    _jieba_dict_loaded = True
EVENT_KEYWORDS_ZH = [
    "冲突", "战斗", "空袭", "武装", "交火", "爆炸", "袭击", "制裁",
    "政变", "选举", "抗议", "暴动", "难民", "停火", "和谈",
    "贸易", "投资", "管道", "港口", "铁路", "经济走廊"
]
EVENT_KEYWORDS_EN = [
    "conflict", "attack", "airstrike", "ceasefire", "sanction",
    "coup", "election", "protest", "refugee", "trade", "investment",
    "pipeline", "port", "railway", "economic corridor"
]


class NERExtractor:
    """命名实体识别器，支持中英文文本"""

    def __init__(self, jieba_fallback: bool = False):
        """懒加载模型。

        :param jieba_fallback: 是否在领域 gazetteer 词典对整段零命中时启用 jieba
            词性标注做召回兜底。默认关闭——batch1 人工金标准实测该兜底在 4 段
            零命中文本上净增 34 个假阳性、0 个真阳性，反把 micro-F1 从 0.494 拉低
            到 0.478，故仅作为面向域外文本的显式可选项保留。
        """
        self._lac = None
        self._nlp_en = None
        self._jieba_fallback = jieba_fallback
        self._backend_state = threading.local()

    @property
    def last_backend(self):
        return getattr(self._backend_state, 'name', 'not_run')

    def _ensure_lac_loaded(self):
        """确保 LAC 中文模型已加载"""
        if self._lac is None:
            if LAC is None:
                raise ImportError("LAC 未安装，请运行: pip install lac==2.1.2")
            self._lac = LAC(mode="lac")
            logger.info("[NER] LAC 中文模型加载完成")

    def _ensure_spacy_loaded(self):
        """确保 spaCy 英文模型已加载"""
        if self._nlp_en is None:
            if spacy is None:
                raise ImportError("spaCy 未安装，请运行: pip install spacy && python -m spacy download en_core_web_sm")
            self._nlp_en = spacy.load("en_core_web_sm")
            logger.info("[NER] spaCy 英文模型加载完成")

    def _detect_language(self, text: str) -> str:
        """简单语言检测：基于中文字符比例"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        total_chars = len(text.strip())
        if total_chars == 0:
            return "zh"
        return "zh" if chinese_chars / total_chars > 0.3 else "en"

    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """
        从文本中提取命名实体（自动判断语言）

        :param text: 输入文本
        :return: {
            "locations": ["缅甸", "仰光", ...],
            "organizations": ["联合国", ...],
            "persons": ["昂山素季", ...],
            "events": ["武装冲突", ...]
        }
        """
        self._backend_state.name = 'failed'
        if not text or not text.strip():
            self._backend_state.name = 'empty_input'
            return {
                "locations": [],
                "organizations": [],
                "persons": [],
                "events": [],
            }

        lang = self._detect_language(text)

        if lang == "zh":
            entities = self._extract_zh(text)
        else:
            entities = self._extract_en(text)

        # 提取事件关键词
        entities["events"] = self._extract_events(text, lang)

        # 清洗：过滤脏文本产生的垃圾实体（整句/图注/页脚残留）
        for key in ("locations", "organizations", "persons"):
            entities[key] = self._clean_entity_list(entities.get(key, []))

        return entities

    @staticmethod
    def _clean_entity_list(items: List[str], max_len: int = 30,
                            cap: int = 30) -> List[str]:
        """过滤垃圾实体：过长（多为整句/图注）、过短、纯数字标点、含换行。

        用户上传的网页原文常含图注/页脚/推荐位文本，NER 会把整段
        句子误判为实体，需在源头清洗避免前端展示混乱。
        """
        seen = set()
        cleaned = []
        for item in items:
            if not isinstance(item, str):
                continue
            item = item.strip().strip("·|·")
            if len(item) < 2 or len(item) > max_len:
                continue
            if "\n" in item or "\r" in item:
                continue
            # 纯数字/标点/空白不是有效实体
            if not any(ch.isalnum() for ch in item):
                continue
            low = item.lower()
            if low in seen:
                continue
            seen.add(low)
            cleaned.append(item)
            if len(cleaned) >= cap:
                break
        return cleaned

    def _extract_zh(self, text: str) -> Dict[str, List[str]]:
        """中文 NER 后端优先级：LAC 统计模型 > 领域 gazetteer 词典（默认终点）。

        经 batch1 人工金标准实测：领域 gazetteer 词典 micro-F1 0.494（P 0.770）
        优于通用 jieba 词性标注 0.421（P 0.416，过度标注致精确率崩塌）；即便仅
        在词典零命中时用 jieba 兜底，也在 4 段文本上净增 34 FP / 0 TP，把整体
        拉低到 0.478。故默认以 gazetteer 词典为中文回退终点，jieba 兜底改由构造
        参数 jieba_fallback 显式开启，供域外文本的召回实验使用。
        """
        if LAC is not None:
            try:
                result = self._extract_zh_lac(text)
                self._backend_state.name = 'lac'
                return result
            except Exception as e:
                logger.warning('[NER] LAC不可用，回退词典: %s', type(e).__name__)
        result = self._extract_zh_regex(text)
        # 默认：领域 gazetteer 词典即中文回退终点（实测 micro-F1 0.494，P 0.770）
        if any(result.values()) or pseg is None or not self._jieba_fallback:
            self._backend_state.name = 'dictionary_regex'
            return result
        # 仅当显式开启 jieba_fallback 且词典对整段零命中时，才用词性标注兜底召回
        self._backend_state.name = 'jieba_posseg'
        return self._extract_zh_jieba(text)

    def _extract_zh_lac(self, text: str) -> Dict[str, List[str]]:
        """中文 NER：使用 LAC"""
        self._ensure_lac_loaded()

        result = self._lac.run(text)
        entities = {"locations": [], "organizations": [], "persons": []}

        if result and len(result) >= 2:
            words, tags = result[0], result[1]
            for word, tag in zip(words, tags):
                if tag in ("LOC", "GPE"):
                    entities["locations"].append(word)
                elif tag == "ORG":
                    entities["organizations"].append(word)
                elif tag == "PER":
                    entities["persons"].append(word)

        for key in entities:
            entities[key] = list(dict.fromkeys(entities[key]))
        return entities

    def _extract_zh_jieba(self, text: str) -> Dict[str, List[str]]:
        """中文 NER 回退：jieba 词性标注（ns/nr/nt）"""
        _ensure_jieba_dict()
        entities = {"locations": [], "organizations": [], "persons": []}
        for word, flag in pseg.cut(text):
            if flag == "ns":
                entities["locations"].append(word)
            elif flag == "nt":
                entities["organizations"].append(word)
            elif flag == "nr":
                entities["persons"].append(word)
        for key in entities:
            entities[key] = list(dict.fromkeys(entities[key]))
        return entities

    def _extract_zh_regex(self, text: str) -> Dict[str, List[str]]:
        """中文 NER 最后回退：词典正则匹配"""
        entities = {"locations": [], "organizations": [], "persons": []}
        for word, flag in JIEBA_GEO_DICT:
            if word in text:
                if flag == "ns":
                    entities["locations"].append(word)
                elif flag == "nt":
                    entities["organizations"].append(word)
                elif flag == "nr":
                    entities["persons"].append(word)
        return entities

    def _extract_en(self, text: str) -> Dict[str, List[str]]:
        """英文 NER：使用 spaCy"""
        self._ensure_spacy_loaded()

        doc = self._nlp_en(text)
        self._backend_state.name = 'spacy:en_core_web_sm'
        entities = {"locations": [], "organizations": [], "persons": []}

        for ent in doc.ents:
            # 词典优先：spaCy 小模型常把缅甸地名误判为 PERSON
            if is_known_en_location(ent.text):
                entities["locations"].append(ent.text)
            elif ent.label_ in ("GPE", "LOC"):
                entities["locations"].append(ent.text)
            elif ent.label_ == "ORG":
                entities["organizations"].append(ent.text)
            elif ent.label_ == "PERSON":
                entities["persons"].append(ent.text)

        # 去重
        for key in entities:
            entities[key] = list(dict.fromkeys(entities[key]))

        return entities

    def _extract_events(self, text: str, lang: str) -> List[str]:
        """从文本中提取事件关键词"""
        keywords = EVENT_KEYWORDS_ZH if lang == "zh" else EVENT_KEYWORDS_EN
        text_lower = text.lower() if lang == "en" else text

        found = []
        for kw in keywords:
            if kw in text_lower:
                found.append(kw)

        return found

    def extract_batch(self, texts: List[str]) -> List[Dict[str, List[str]]]:
        """批量提取实体"""
        return [self.extract_entities(text) for text in texts]


# 模块级单例
_ner_instance = None
_ner_lock = threading.Lock()


def get_ner_extractor() -> NERExtractor:
    """获取全局 NER 单例（线程安全）"""
    global _ner_instance
    if _ner_instance is None:
        with _ner_lock:
            if _ner_instance is None:
                _ner_instance = NERExtractor()
    return _ner_instance
