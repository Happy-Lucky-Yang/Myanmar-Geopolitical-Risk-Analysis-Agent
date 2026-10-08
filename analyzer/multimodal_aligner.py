"""
analyzer.multimodal_aligner - 多模态时空对齐

process.html 要求: "遥感影像与文本事件的时空匹配（基于时间戳与地理坐标）"

功能:
  - 按月份对齐夜光指数与同期新闻冲突频次
  - 按省份关联遥感变化与 GDELT 地理坐标事件
  - 输出对齐矩阵: [(month, nightlight_delta, conflict_count, sentiment_avg)]
  - 相关性分析: Pearson 相关系数

集成: 趋势页面增加"多源融合视图"切换
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LegacyMultimodalAligner:
    """多模态时空数据对齐器"""

    def align_monthly(self, months: int = 12) -> List[Dict]:
        """
        按月份对齐多源数据

        :param months: 对齐最近多少个月
        :return: 对齐矩阵
            [{"month": "2025-01",
              "nightlight": 0.45,
              "nightlight_delta": -0.02,
              "conflict_count": 15,
              "sentiment_avg": 0.65,
              "gdelt_events": 8,
              "economic_index": 0.42}]
        """
        # 1. 获取夜光月度序列
        nightlight_series = self._get_nightlight_series(months)

        # 2. 获取新闻冲突月度统计
        conflict_series = self._get_conflict_series(months)

        # 3. 获取情感月度序列
        sentiment_series = self._get_sentiment_series(months)

        # 4. 获取 GDELT 月度统计
        gdelt_series = self._get_gdelt_series(months)

        # 5. 对齐
        aligned = []
        now = datetime.now()

        for i in range(months):
            # 计算月份标签
            month_dt = now - timedelta(days=30 * (months - 1 - i))
            month_key = month_dt.strftime("%Y-%m")

            nl = nightlight_series.get(month_key, 0.5)
            nl_prev = nightlight_series.get(
                (month_dt - timedelta(days=30)).strftime("%Y-%m"), nl
            )
            nl_delta = round(nl - nl_prev, 4)

            conflict = conflict_series.get(month_key, 0)
            sentiment = sentiment_series.get(month_key, 0.5)
            gdelt = gdelt_series.get(month_key, 0)

            aligned.append({
                "month": month_key,
                "nightlight": round(nl, 4),
                "nightlight_delta": nl_delta,
                "conflict_count": conflict,
                "sentiment_avg": round(sentiment, 4),
                "gdelt_events": gdelt,
            })

        return aligned

    def compute_correlations(self, aligned_data: List[Dict] = None) -> Dict:
        """
        计算多源数据间的相关性

        :param aligned_data: 对齐后的数据 (若为 None 则自动获取)
        :return: 相关系数矩阵
        """
        if aligned_data is None:
            aligned_data = self.align_monthly()

        if len(aligned_data) < 3:
            return {"error": "数据不足"}

        # 提取序列
        nl_deltas = [d["nightlight_delta"] for d in aligned_data]
        conflicts = [d["conflict_count"] for d in aligned_data]
        sentiments = [d["sentiment_avg"] for d in aligned_data]

        correlations = {
            "nightlight_vs_conflict": self._pearson(nl_deltas, conflicts),
            "nightlight_vs_sentiment": self._pearson(nl_deltas, sentiments),
            "conflict_vs_sentiment": self._pearson(conflicts, sentiments),
        }

        return correlations

    def get_province_alignment(self) -> List[Dict]:
        """
        按省份对齐遥感变化与事件数据

        :return: 省级对齐列表
        """
        from visualization.map_gen import MYANMAR_PROVINCES

        results = []
        for province, (lat, lon) in MYANMAR_PROVINCES.items():
            # 获取该省份的事件数量 (简化: 基于风险历史)
            event_count = self._estimate_province_events(province)

            # 夜光代理: 基于省份经济活跃度估算
            nl_proxy = self._estimate_province_nightlight(province)

            results.append({
                "province": province,
                "lat": lat,
                "lon": lon,
                "nightlight_proxy": round(nl_proxy, 4),
                "event_count": event_count,
                "risk_level": "高" if event_count > 10 else "中" if event_count > 3 else "低",
            })

        return results

    # ============================================================
    # 内部数据获取方法
    # ============================================================

    def _get_nightlight_series(self, months: int) -> Dict[str, float]:
        """获取夜光月度序列"""
        try:
            from data.nightlight_crawler import get_nightlight_crawler
            nl = get_nightlight_crawler()
            series = nl.get_monthly_series(months=months)
            return {item["month"]: item["value"] for item in series}
        except Exception as e:
            logger.debug(f"[Aligner] 夜光序列不可用: {e}")
            return self._generate_synthetic_series(months, base=0.5, noise=0.05)

    def _get_conflict_series(self, months: int) -> Dict[str, int]:
        """获取冲突频次月度统计"""
        try:
            from analyzer.data_loader import get_data_loader
            loader = get_data_loader()
            history = loader.load_risk_history(days=months * 30)

            conflict_by_month = {}
            for record in history:
                date = record.get("date", "")
                month_key = date[:7]  # YYYY-MM
                details = record.get("details", {})
                cf = details.get("conflict_frequency", 0)
                if month_key:
                    conflict_by_month[month_key] = conflict_by_month.get(month_key, 0) + (1 if cf > 0.5 else 0)

            return conflict_by_month
        except Exception as e:
            logger.debug(f"[Aligner] 冲突序列不可用: {e}")
            return {}

    def _get_sentiment_series(self, months: int) -> Dict[str, float]:
        """获取情感月度序列"""
        try:
            from analyzer.data_loader import get_data_loader
            loader = get_data_loader()
            history = loader.load_risk_history(days=months * 30)

            sentiment_by_month = {}
            count_by_month = {}
            for record in history:
                date = record.get("date", "")
                month_key = date[:7]
                details = record.get("details", {})
                sent = details.get("sentiment_avg", 0.5)
                if month_key:
                    sentiment_by_month[month_key] = sentiment_by_month.get(month_key, 0) + sent
                    count_by_month[month_key] = count_by_month.get(month_key, 0) + 1

            # 计算月均情感
            for mk in sentiment_by_month:
                sentiment_by_month[mk] /= max(count_by_month.get(mk, 1), 1)

            return sentiment_by_month
        except Exception:
            return self._generate_synthetic_series(months, base=0.5, noise=0.1)

    def _get_gdelt_series(self, months: int) -> Dict[str, int]:
        """获取 GDELT 月度事件统计 (简化: 使用当前 GDELT 数据)"""
        # 简化实现: GDELT 通常只有最近几天数据
        return {}

    def _estimate_province_events(self, province: str) -> int:
        """估算省份事件数 (简化)"""
        # 边境省份事件更多
        border = {"掸邦": 15, "克钦邦": 12, "克伦邦": 10, "若开邦": 14, "钦邦": 6}
        return border.get(province, 3)

    def _estimate_province_nightlight(self, province: str) -> float:
        """估算省份夜光代理值"""
        # 经济活跃省份夜光更高
        active = {"仰光省": 0.7, "曼德勒省": 0.65, "内比都": 0.7}
        conflict = {"掸邦": 0.3, "克钦邦": 0.25, "若开邦": 0.2, "克伦邦": 0.3}
        if province in active:
            return active[province]
        if province in conflict:
            return conflict[province]
        return 0.5

    def _generate_synthetic_series(self, months: int, base: float = 0.5,
                                   noise: float = 0.05) -> Dict[str, float]:
        """生成合成序列 (数据不可用时降级)"""
        import random
        now = datetime.now()
        series = {}
        value = base
        for i in range(months):
            month_dt = now - timedelta(days=30 * (months - 1 - i))
            month_key = month_dt.strftime("%Y-%m")
            value += random.uniform(-noise, noise)
            value = max(0.1, min(0.9, value))
            series[month_key] = round(value, 4)
        return series

    @staticmethod
    def _pearson(x: list, y: list) -> float:
        """计算 Pearson 相关系数"""
        n = min(len(x), len(y))
        if n < 3:
            return 0.0

        x, y = x[:n], y[:n]
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        den_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
        den_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5

        if den_x * den_y == 0:
            return 0.0

        return round(num / (den_x * den_y), 4)


# ============================================================
# 单例
# ============================================================
class MultimodalAligner:
    """正式对齐只读取持久观测；保留年度粒度，不生成补点或代理夜光。"""

    @staticmethod
    def observations():
        from storage.repository import get_repository
        repo = get_repository()
        if repo is not None:
            from sqlalchemy import select
            t = repo.schema.indicator_observations
            with repo.engine.connect() as conn:
                rows = conn.execute(select(t, repo.schema.sources.c.name.label('source')).join(
                    repo.schema.sources, t.c.source_id == repo.schema.sources.c.id)).mappings().all()
            return [{**r['payload'], **{k: v for k, v in r.items() if k != 'payload'}} for r in rows]
        import json
        from pathlib import Path
        from utils.config import get_data_paths
        path = Path(get_data_paths()['external']) / 'indicator_observations.jsonl'
        if not path.exists():
            return []
        result = []
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError()
                result.append(row)
            except ValueError:
                raise ValueError(f'观测文件第{number}行损坏；未使用合成回填') from None
        return result

    @staticmethod
    def month_keys(months, end_date=None):
        from utils.data_contract import business_date
        if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 120:
            raise ValueError('months必须为1～120的整数')
        end = business_date() if end_date is None else business_date(end_date)
        if end is None:
            raise ValueError('无效截止日')
        last = end.year * 12 + end.month - 1
        return [f'{i // 12:04}-{i % 12 + 1:02}' for i in range(last - months + 1, last + 1)]

    def align_monthly(self, months=12, end_date=None, region='MMR', source=None,
                      observations=None, news=None, events=None, start_date=None):
        from utils.data_contract import business_date, finite_number, fingerprint, FORMAL_MODES
        from analyzer.spatial_analysis import unique_events, locate_event
        from data.gdelt_files import event_category
        from analyzer.data_loader import get_data_loader
        from data.event_store import get_event_store
        keys = self.month_keys(months, end_date)
        end = business_date() if end_date is None else business_date(end_date)
        observations = self.observations() if observations is None else observations
        loader = get_data_loader()
        raw_news = loader.load_raw_news() if news is None else news
        news = loader.preprocess([a for a in raw_news if isinstance(a, dict) and a.get('run_kind') in FORMAL_MODES])
        events = unique_events(get_event_store().load(days=None) if events is None else events, source=source)
        observations = [o for o in observations if isinstance(o, dict) and o.get('run_kind', 'existing') in FORMAL_MODES
                        and o.get('data_domain', 'observed') == 'observed']
        start = business_date(start_date) if start_date is not None else None
        if start_date is not None:
            if start is None or start > end:
                raise ValueError('无效起始日')
            first, last = start.year * 12 + start.month - 1, end.year * 12 + end.month - 1
            keys = [f'{i // 12:04}-{i % 12 + 1:02}' for i in range(first, last + 1)]
        variants = {}
        for observation in observations:
            if observation.get('id') is not None:
                variants.setdefault(str(observation['id']), set()).add(fingerprint(observation))
        conflicting_ids = {identity for identity, values in variants.items() if len(values) > 1}
        result = []
        for month in keys:
            period_start = business_date(month + '-01')
            period_end = (period_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            if (start and period_start < start) or period_end > end + timedelta(days=1):
                continue
            evs = [e for e in events if e['date'][:7] == month and business_date(e['date']) <= end
                   and (region == 'MMR' or locate_event(e)[0] == region)]
            articles = [a for a in news if a.get('pub_time', '')[:7] == month
                        and business_date(a['pub_time']) <= end
                        and (not source or source in a.get('sources', []))
                        and (region == 'MMR' or (a.get('event_location') and locate_event(a)[0] == region))]
            sentiments = [a['sentiment_score'] for a in articles if finite_number(a.get('sentiment_score'))
                          and 0 <= a['sentiment_score'] <= 1]
            obs = [o for o in observations if o.get('indicator') == 'nightlight' and o.get('frequency') == 'monthly'
                   and o.get('quality') == 'observed' and o.get('region') == region
                   and business_date(o.get('period_start')) == period_start
                   and business_date(o.get('period_end')) == period_end
                   and finite_number(o.get('value')) and (o.get('source') or o.get('source_id'))
                   and isinstance(o.get('unit'), str) and o['unit'].strip()
                   and (not source or o.get('source') == source)]
            # 多个来源/版本未经校准不能平均为一个夜光值；保留待判断状态。
            obs = [item for _, item in sorted({fingerprint(o): o for o in obs}.items())]
            selected = obs[0] if len(obs) == 1 and str(obs[0].get('id')) not in conflicting_ids else None
            nl = selected['value'] if selected else None
            classified = [e for e in evs if event_category(e) != 'unknown']
            series_key = fingerprint([selected.get('source') or selected.get('source_id'), selected['unit'],
                                      selected.get('product'), selected.get('dataset_version')]) if selected else None
            result.append({'month': month, 'frequency': 'monthly', 'nightlight': nl,
                           'period_start': period_start.isoformat(), 'period_end': period_end.isoformat(),
                           'period_end_exclusive': True, 'nightlight_series_key': series_key,
                           'nightlight_delta': None, 'conflict_count': sum(event_category(e) == 'conflict' for e in classified) if classified else None,
                           'sentiment_avg': 1 - sum(sentiments) / len(sentiments) if sentiments else None,
                           'gdelt_events': len(evs) if evs else None,
                           'quality': {'nightlight': 'observed' if nl is not None else 'ambiguous' if obs else 'missing',
                                       'conflict_count': 'derived' if classified else 'missing',
                                       'sentiment_avg': 'derived' if sentiments else 'missing'},
                           'observation_ids': {'nightlight': str(selected.get('id') or fingerprint(selected)) if selected else None,
                                               'conflict_count': month if classified else None, 'sentiment_avg': month if sentiments else None},
                           'units': {'nightlight': selected['unit'] if selected else None,
                                     'conflict_count': '冲突类事件记录数', 'sentiment_avg': '情感风险指数0～1'},
                           'warnings': ['夜光存在多来源、多版本或同ID冲突，未选择任一值'] if obs and not selected else [],
                           'sample_count': {'events': len(evs), 'sentiment_articles': len(sentiments)},
                           'sources': sorted({str(e.get('source', 'gdelt')) for e in evs} |
                                             {str(o.get('source') or o.get('source_id')) for o in obs} |
                                             {str(s) for a in articles for s in a.get('sources', [])}),
                           'algorithm_version': 'multimodal-v3', 'data_status': 'partial'})
        for previous, current in zip(result, result[1:]):
            if (finite_number(previous['nightlight']) and finite_number(current['nightlight'])
                    and previous['period_end'] == current['period_start']
                    and previous['nightlight_series_key'] == current['nightlight_series_key']):
                current['nightlight_delta'] = current['nightlight'] - previous['nightlight']
                current['quality']['nightlight_delta'] = 'derived'
                current['observation_ids']['nightlight_delta'] = current['observation_ids']['nightlight']
        return result

    def compute_correlations(self, aligned_data=None):
        from utils.data_contract import finite_number
        import numpy as np
        rows = self.align_monthly() if aligned_data is None else aligned_data
        pairs = {'nightlight_vs_conflict': ('nightlight_delta', 'conflict_count'),
                 'nightlight_vs_sentiment': ('nightlight_delta', 'sentiment_avg'),
                 'conflict_vs_sentiment': ('conflict_count', 'sentiment_avg')}
        result, counts, reasons = {}, {}, {}
        for name, (left, right) in pairs.items():
            used_left, used_right, used_periods, selected = set(), set(), set(), []
            series_keys = set()
            for row in rows:
                ids, quality = row.get('observation_ids', {}), row.get('quality', {})
                period = row.get('month')
                if (row.get('frequency') != 'monthly' or not period or period in used_periods
                    or not ids.get(left) or not ids.get(right) or ids[left] in used_left or ids[right] in used_right
                    or quality.get(left) not in {'observed', 'derived'} or quality.get(right) not in {'observed', 'derived'}
                    or not finite_number(row.get(left)) or not finite_number(row.get(right))):
                    continue
                used_periods.add(period)
                used_left.add(ids[left]); used_right.add(ids[right])
                selected.append((row[left], row[right]))
                if left.startswith('nightlight'):
                    series_keys.add(row.get('nightlight_series_key', 'unspecified'))
            counts[name] = len(selected)
            value = None
            if len(selected) >= 12 and len(series_keys) <= 1:
                x, y = np.asarray(selected, dtype=float).T
                if x.std() > 1e-12 and y.std() > 1e-12:
                    value = round(float(np.corrcoef(x, y)[0, 1]), 4)
            result[name] = value
            reasons[name] = ('夜光序列来源/量纲或版本不一致，未混合计算' if len(series_keys) > 1 else
                             '探索性相关；未校正时间自相关，不作因果解释' if value is not None else
                             '需12个不重复的完整月配对观测且双方非零方差')
        return {**result, 'sample_counts': counts, 'reasons': reasons, 'algorithm_version': 'multimodal-v3'}

    def align_annual(self, days=366, end_date=None, region='MMR', source=None, observations=None):
        from utils.data_contract import time_window, business_date, normalize_observation, fingerprint, FORMAL_MODES
        start, end = time_window(days, end_date)
        observations = self.observations() if observations is None else observations
        rows, invalid = {}, 0
        for raw in observations:
            if not isinstance(raw, dict) or raw.get('frequency') != 'annual':
                continue
            if (raw.get('run_kind') not in FORMAL_MODES or raw.get('quality') != 'observed'
                    or raw.get('data_domain', 'observed') != 'observed'
                    or raw.get('region') != region or (source and raw.get('source') != source)):
                continue
            try:
                row = normalize_observation(raw)
            except (ValueError, TypeError):
                invalid += 1
                continue
            if start <= business_date(row['period_start']) and business_date(row['period_end']) <= end:
                rows[fingerprint(row)] = row
        ordered = sorted(rows.values(), key=lambda r: (r['period_start'], r['indicator'], r['source'], r['dataset_version'], r['id'], fingerprint(r)))
        return {'observations': ordered, 'invalid_count': invalid,
                'note': '年度原值独立展示；仅包含窗口内完整年，不插值、不参与月度相关，不选择冲突版本'}

    def get_province_alignment(self, days=30, end_date=None, source=None, region='MMR'):
        from data.event_store import get_event_store
        from analyzer.spatial_analysis import aggregate_provinces
        return [{**r, 'nightlight_proxy': None, 'nightlight': None,
                 'nightlight_status': 'missing', 'note': '尚无真实省级夜光栅格观测'}
                for r in aggregate_provinces(get_event_store().load(days, end_date), days, end_date, source)
                if region == 'MMR' or r['region'] == region]


_instance = None
_lock = threading.Lock()


def get_multimodal_aligner() -> MultimodalAligner:
    """获取全局多模态对齐器单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = MultimodalAligner()
    return _instance
