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

# 中文 NER：百度 LAC
try:
    from LAC import LAC
except ImportError:
    LAC = None

# 英文 NER：spaCy
try:
    import spacy
except ImportError:
    spacy = None

# 地缘政治事件关键词（用于辅助事件实体识别）
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
        lang = self._detect_language(text)

        if lang == "zh":
            entities = self._extract_zh(text)
        else:
            entities = self._extract_en(text)

        # 提取事件关键词
        entities["events"] = self._extract_events(text, lang)

        return entities

    def _extract_zh(self, text: str) -> Dict[str, List[str]]:
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

        # 去重
        for key in entities:
            entities[key] = list(dict.fromkeys(entities[key]))  # 保持顺序去重

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
