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
except ImportError:
    LAC = None

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

    def __init__(self):
        """懒加载模型"""
        self._lac = None
        self._nlp_en = None

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
        if not text or not text.strip():
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

        return entities

    def _extract_zh(self, text: str) -> Dict[str, List[str]]:
        """中文 NER：优先 LAC，回退 jieba 词性标注"""
        if LAC is not None:
            try:
                return self._extract_zh_lac(text)
            except Exception as e:
                logger.warning(f"[NER] LAC 不可用，回退 jieba: {e}")
        if pseg is not None:
            return self._extract_zh_jieba(text)
        # 最后回退：正则
        return self._extract_zh_regex(text)

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
        entities = {"locations": [], "organizations": [], "persons": []}

        for ent in doc.ents:
            if ent.label_ in ("GPE", "LOC"):
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
