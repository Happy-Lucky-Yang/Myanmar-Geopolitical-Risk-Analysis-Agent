"""
analyzer.alert_monitor - 动态预警面板

process.html 要求: "关键阈值监测、实时风险提示、辅助决策"

功能:
  - 定义预警阈值: 风险分>=80(红色)、>=60(橙色)、>=40(黄色)
  - 正式日指标持续确认、滞回与去重；缺测/过期不触发
  - 预警历史: PostgreSQL 或 DATA_ROOT/raw/alerts.json，保留确认审计
  - 当前预警状态查询接口

前端:
  - 导航栏预警指示灯 (红/橙/绿)
  - 预警弹窗/横幅通知
  - 趋势页面阈值参考线
"""
import os
import json
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 预警等级定义
# ============================================================
ALERT_LEVELS = {
    "red": {"threshold": 80, "label": "红色预警", "color": "#f85149",
            "description": "风险极高，需立即关注"},
    "orange": {"threshold": 60, "label": "橙色预警", "color": "#d29922",
               "description": "风险较高，需密切关注"},
    "yellow": {"threshold": 40, "label": "黄色预警", "color": "#e3b341",
               "description": "风险中等，建议关注"},
    "green": {"threshold": 0, "label": "未达预警阈值", "color": "#3fb950",
              "description": "风险较低"},
}

from pathlib import Path
from datetime import timedelta
from utils.config import get_data_paths, load_config
from utils.data_contract import (business_date, finite_number, fingerprint, json_safe,
                                 select_history, utc_now, RISK_VERSION)


class AlertMonitor:
    """正式日指标的可复现状态机；查询不创建预警，写入不裁剪历史。"""

    def __init__(self, config=None, repository=None):
        from storage.repository import get_repository
        self.repo = repository if repository is not None else get_repository()
        self.path = Path(get_data_paths()['raw']) / 'alerts.json' if self.repo is None else None
        self._lock = threading.RLock()
        cfg = load_config().get('alerts', {}) if config is None else config
        self.persistence = cfg.get('persistence_days', 2)
        self.hysteresis = cfg.get('hysteresis_points', 5)
        self.min_coverage = cfg.get('min_indicator_coverage', 0.5)
        self.thresholds = {key: cfg.get('thresholds', {}).get(key, ALERT_LEVELS[key]['threshold'])
                           for key in ('green', 'yellow', 'orange', 'red')}
        values = list(self.thresholds.values())
        if (type(self.persistence) is not int or not 2 <= self.persistence <= 30
                or not finite_number(self.hysteresis) or not 0 <= self.hysteresis <= 20
                or not finite_number(self.min_coverage) or not 0 <= self.min_coverage <= 1
                or any(not finite_number(v) for v in values)
                or not (values[0] == 0 < values[1] < values[2] < values[3] <= 100)):
            raise ValueError('预警阈值、持续天数、滞回或覆盖率配置无效')
        self.version = 'alert-v2-' + fingerprint([self.thresholds, self.persistence,
                                                self.hysteresis, self.min_coverage])[:12]

    def _eligible(self, row):
        details = row.get('details', {})
        coverage = details.get('indicator_coverage', row.get('indicator_coverage'))
        return (finite_number(coverage) and coverage >= self.min_coverage
                and type(row.get('sample_count')) is int and row['sample_count'] > 0
                and bool(row.get('sources'))
                and all(not obj.get(flag) for obj in (row, details)
                        for flag in ('stale', 'synthetic', 'imputed', 'estimated'))
                and all(obj.get('quality', 'derived') in {'observed', 'derived'} for obj in (row, details))
                and all(obj.get('data_status', 'partial') in {'ok', 'partial'} for obj in (row, details)))

    def _score_to_level(self, score):
        return next(key for key in ('red', 'orange', 'yellow', 'green') if score >= self.thresholds[key])

    def evaluate(self, records, end_date=None, region='MMR', algorithm_version=RISK_VERSION):
        """按连续业务日重放；不同版本、缺测日与质量不合格日均不能连接持续性。"""
        end = business_date() if end_date is None else business_date(end_date)
        if end is None:
            raise ValueError('无效截止日期')
        rows = select_history(records, 3660, end, algorithm_version=algorithm_version, region=region)
        state = pending = previous_day = episode = None
        streak = 0
        transitions = []
        latest = None
        for row in rows:
            day = business_date(row['date'])
            if not self._eligible(row):
                state = pending = previous_day = episode = None
                streak = 0
                continue
            if previous_day is None or day != previous_day + timedelta(days=1):
                state = pending = episode = None
                streak = 0
            previous_day, latest = day, row
            target = self._score_to_level(row['risk_score'])
            if (state and self.thresholds[target] < self.thresholds[state]
                    and row['risk_score'] >= self.thresholds[state] - self.hysteresis):
                target = state
            if target != state:
                streak = streak + 1 if pending == target else 1
                pending = target
                if streak >= self.persistence:
                    old_state, state = state, target
                    episode = {**ALERT_LEVELS[state], 'level': state,
                               'id': fingerprint([day.isoformat(), region, algorithm_version, self.version, state]),
                               'date': day.isoformat(), 'region': region, 'algorithm_version': self.version,
                               'risk_version': algorithm_version, 'risk_score': row['risk_score'],
                               'previous_level': old_state, 'sources': row['sources'],
                               'sample_count': row['sample_count'], 'acknowledged': False,
                               'input_hash': row.get('input_hash'), 'run_kind': row['run_kind']}
                    transitions.append(episode)
                    pending, streak = None, 0
            else:
                pending, streak = None, 0
        fresh = latest is not None and previous_day == end
        level = state if fresh and state else 'unknown'
        info = ALERT_LEVELS.get(level, {'label': '待持续确认' if fresh else '缺测或过期', 'color': '#8b949e'})
        return {'level': level, 'label': info['label'], 'color': info['color'],
                'risk_score': latest['risk_score'] if fresh else None,
                'latest_observation': latest['date'] if latest else None,
                'data_status': 'ok' if level != 'unknown' else 'pending' if fresh else 'insufficient',
                'pending_level': pending if fresh else None, 'persistence_days': self.persistence,
                'hysteresis_points': self.hysteresis, 'algorithm_version': self.version,
                'description': '阈值规则提示，非发生概率；质量不足时不能解释为低风险。',
                'latest_alert': episode if fresh and state not in {None, 'green'} else None,
                'transitions': transitions}

    def _history(self, end_date, region, algorithm_version):
        from analyzer.data_loader import get_data_loader
        return get_data_loader().load_risk_history(days=3660, end_date=end_date,
                                                   region=region, algorithm_version=algorithm_version)

    def check_history(self, records=None, end_date=None, region='MMR', algorithm_version=RISK_VERSION):
        """仅流水线提交正式日指标后调用；历史回放不得补发实时预警。"""
        end = business_date() if end_date is None else business_date(end_date)
        if end != business_date():
            return []
        records = self._history(end, region, algorithm_version) if records is None else records
        result = self.evaluate(records, end, region, algorithm_version)
        current = [r for r in result['transitions'] if r['date'] == end.isoformat()]
        return [row for row in current if self._save_alert(row)]

    def check_risk_score(self, risk_score, details=None):
        """兼容旧调用但不写正式预警：单个分值不具备持续性和来源契约。"""
        return None

    def get_current_status(self, end_date=None, region='MMR', algorithm_version=RISK_VERSION):
        result = self.evaluate(self._history(end_date, region, algorithm_version), end_date, region, algorithm_version)
        result.pop('transitions')
        episode = result['latest_alert']
        if episode:
            persisted = {r['id']: r for r in self.get_alert_history(limit=None, region=region)}
            result['latest_alert'] = persisted.get(episode['id'], episode)
        result['active_alerts'] = int(bool(episode and not result['latest_alert'].get('acknowledged')))
        return result

    def get_alert_history(self, limit=20, region='MMR', end_date=None):
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError('limit 必须为正整数')
        end = business_date() if end_date is None else business_date(end_date)
        if end is None:
            raise ValueError('无效截止日期')
        with self._lock:
            if self.repo is None:
                rows = self._load_file()
            else:
                from sqlalchemy import select
                t = self.repo.schema.alerts
                with self.repo.engine.connect() as conn:
                    rows = [{**r['payload'], 'id': r['id'], 'date': r['date'].isoformat(),
                             'acknowledged': r['acknowledged_at'] is not None,
                             'acknowledged_at': json_safe(r['acknowledged_at']), 'acknowledged_by': r['acknowledged_by']}
                            for r in conn.execute(select(t).where(t.c.region == region, t.c.date <= end)).mappings()]
            rows = [r for r in rows if r.get('region', 'MMR') == region
                    and (business_date(r.get('date') or r.get('triggered_at')) or end) <= end]
            rows.sort(key=lambda r: (str(r.get('date', '')), str(r.get('triggered_at', '')), r['id']), reverse=True)
            return rows if limit is None else rows[:limit]

    def get_threshold_lines(self):
        return [{'yAxis': self.thresholds[name], 'name': ALERT_LEVELS[name]['label'], 'color': ALERT_LEVELS[name]['color']}
                for name in ('red', 'orange', 'yellow')]

    def acknowledge_alert(self, alert_id, user_id=None):
        if not isinstance(alert_id, str) or not alert_id:
            raise ValueError('alert_id 必须是非空字符串')
        now = utc_now()
        with self._lock:
            if self.repo is not None:
                from sqlalchemy import select, update
                from uuid import uuid4
                if not user_id:
                    raise ValueError('数据库预警确认必须关联用户')
                t = self.repo.schema.alerts
                with self.repo.write_transaction() as conn:
                    row = conn.execute(select(t).where(t.c.id == alert_id).with_for_update()).mappings().first()
                    if row is None:
                        return False
                    if row['acknowledged_at'] is None:
                        conn.execute(update(t).where(t.c.id == alert_id).values(acknowledged_by=user_id, acknowledged_at=now))
                        self.repo._insert(conn, self.repo.schema.audit_logs, {'id': uuid4().hex,
                            'user_id': user_id, 'action': 'acknowledge_alert', 'target': alert_id, 'payload': {}})
                    return True
            rows = self._load_file()
            for row in rows:
                if row['id'] == alert_id:
                    if not row.get('acknowledged'):
                        row.update(acknowledged=True, acknowledged_at=now.isoformat(), acknowledged_by=user_id or 'local')
                        row.setdefault('audit', []).append({'action': 'acknowledge', 'at': now.isoformat(), 'user': user_id or 'local'})
                        self._write_file(rows)
                    return True
            return False

    def _load_file(self):
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows):
            raise ValueError('预警文件损坏，拒绝覆盖')
        return rows

    def _write_file(self, rows):
        import tempfile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as handle:
            json.dump(json_safe(rows), handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, self.path)

    def _save_alert(self, alert):
        row = {**alert, 'triggered_at': utc_now().isoformat()}
        with self._lock:
            if self.repo is not None:
                from sqlalchemy.dialects.postgresql import insert
                t = self.repo.schema.alerts
                with self.repo.write_transaction() as conn:
                    return conn.execute(insert(t).values(id=row['id'], level=row['level'],
                        date=business_date(row['date']), region=row['region'], algorithm_version=row['algorithm_version'],
                        payload=json_safe(row)).on_conflict_do_nothing()).rowcount == 1
            rows = self._load_file()
            if any(r['id'] == row['id'] for r in rows):
                return False
            self._write_file([*rows, row])
            return True


# ============================================================
# 单例
# ============================================================
_instance = None
_lock = threading.Lock()


def get_alert_monitor() -> AlertMonitor:
    """获取全局预警监测器单例"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = AlertMonitor()
    return _instance
