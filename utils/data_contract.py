"""全模块共用的时间、缺失值、来源与标识契约，不执行 IO。"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    BUSINESS_TZ = ZoneInfo("Asia/Yangon")
except ZoneInfoNotFoundError:
    BUSINESS_TZ = timezone(timedelta(hours=6, minutes=30), "Asia/Yangon")

RISK_VERSION = "risk-v2"
FORMAL_MODES = frozenset({"live", "existing"})
RUN_MODES = FORMAL_MODES | {"manual", "demo", "legacy"}
_TODAY = object()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def business_date(value=_TODAY) -> date | None:
    """无参数取今日；错误值返回 None，绝不伪造为今日。"""
    if value is _TODAY:
        return utc_now().astimezone(BUSINESS_TZ).date()
    if isinstance(value, datetime):
        value = value.replace(tzinfo=BUSINESS_TZ) if value.tzinfo is None else value
        return value.astimezone(BUSINESS_TZ).date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        return business_date(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        for fmt in ("%Y%m%d", "%Y%m%d%H%M%S", "%Y%m%dT%H%M%SZ", "%Y年%m月%d日", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                parsed = datetime.strptime(value, fmt)
                return business_date(parsed.replace(tzinfo=timezone.utc) if value.endswith("Z") else parsed)
            except ValueError:
                continue
    return None


def timestamp_utc(value) -> datetime | None:
    """解析时间戳到 UTC；纯日期不伪造时刻，无时区历史时间按业务时区解释。"""
    if isinstance(value, str):
        if len(value.strip()) <= 10:
            return None
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=BUSINESS_TZ)
    return value.astimezone(timezone.utc)


def time_window(days=30, end_date=None):
    """包含所选日的 N 个连续日历日，返回起始日和排他的结束日。"""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3660:
        raise ValueError("days 必须为 1～3660 的整数")
    end = business_date() if end_date is None else business_date(end_date)
    if end is None:
        raise ValueError("end_date 必须是有效日期")
    return end - timedelta(days=days - 1), end + timedelta(days=1)


def window_utc(days=30, end_date=None):
    start, end = time_window(days, end_date)
    return tuple(datetime.combine(d, time.min, BUSINESS_TZ).astimezone(timezone.utc) for d in (start, end))


def finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def json_safe(value):
    """缺测保持 null，确保 JSON 中没有非标准 NaN/Infinity。"""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def fingerprint(value) -> str:
    payload = json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_url(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username:
            return ""
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(sorted(query)), ""))
    except ValueError:
        return ""


def source_evidence(item):
    """仅保留显式来源与URL配对；旧平行列表不推断对应关系。"""
    evidence = item.get("source_evidence", [])
    evidence = list(evidence) if isinstance(evidence, list) else []
    evidence.append({"source": item.get("source"), "url": item.get("url")})
    pairs = set()
    for entry in evidence:
        if not isinstance(entry, dict):
            continue
        source, url = entry.get("source"), canonical_url(entry.get("url"))
        if isinstance(source, str) and source.strip():
            pairs.add((source.strip(), url))
    return [{"source": source, "url": url} for source, url in sorted(pairs)]


def article_identity(item) -> str:
    text = " ".join(str(item.get("content") or item.get("text") or item.get("title") or "").split())
    return fingerprint({"text": text}) if text else fingerprint({"url": canonical_url(item.get("url"))})


def valid_risk_record(record) -> bool:
    return (isinstance(record, dict) and business_date(record.get("date", "")) is not None
            and finite_number(record.get("risk_score")) and 0 <= record["risk_score"] <= 100
            and isinstance(record.get("details", {}), dict))


def select_history(records, days=30, end_date=None, include_legacy=False,
                   algorithm_version=RISK_VERSION, region="MMR"):
    start, end = time_window(days, end_date)
    by_day = {}
    for row in records:
        if not valid_risk_record(row):
            continue
        day = business_date(row["date"])
        mode = row.get("run_kind", "legacy")
        version = row.get("algorithm_version", "legacy")
        if not start <= day < end or row.get("region", "MMR") != region:
            continue
        if include_legacy:
            if mode != "legacy":
                continue
        elif mode not in FORMAL_MODES or version != algorithm_version:
            continue
        key = day.isoformat()
        # ISO 时间经过归一化后比较；没有时间的 legacy 使用输入次序。
        floor = datetime.min.replace(tzinfo=timezone.utc)
        stamp = timestamp_utc(row.get("recorded_at")) or floor
        previous_stamp = timestamp_utc(by_day.get(key, {}).get("recorded_at")) or floor
        if key not in by_day or stamp >= previous_stamp:
            by_day[key] = {**row, "date": key, "run_kind": mode, "algorithm_version": version}
    return [by_day[k] for k in sorted(by_day)]


def coverage_metadata(records, days=30, end_date=None, **extra):
    start, end = time_window(days, end_date)
    selected = [r for r in records if valid_risk_record(r) and start <= business_date(r["date"]) < end]
    dates = sorted({business_date(r["date"]).isoformat() for r in selected})
    sources = sorted({str(s) for r in selected for s in r.get("sources", [])})
    stamps = [timestamp_utc(r.get("recorded_at")) for r in selected]
    latest = max((s for s in stamps if s is not None), default=None)
    return {
        "requested_start": start.isoformat(), "requested_end": (end - timedelta(days=1)).isoformat(),
        "actual_start": dates[0] if dates else None, "actual_end": dates[-1] if dates else None,
        "valid_days": len(dates), "requested_days": days, "coverage": round(len(dates) / days, 4),
        "timezone": "Asia/Yangon", "unit": "风险分（0-100，非发生概率）",
        "data_status": "empty" if not dates else "partial" if len(dates) < days else "ok",
        "algorithm_version": RISK_VERSION, "sources": sources,
        "latest_observation": dates[-1] if dates else None,
        "analyzed_at": latest.isoformat() if latest else None,
        "stale": bool(dates and dates[-1] < (end - timedelta(days=1)).isoformat()), **extra,
    }


def normalize_observation(record):
    """专用观测导入契约：完整独立周期、原单位和显式版本，不推断真实质量。"""
    if not isinstance(record, dict):
        raise ValueError('观测必须是对象')
    row = dict(record)
    for field, maximum in (('source', 512), ('indicator', 64), ('region', 64),
                           ('unit', 256), ('dataset_version', 128)):
        value = row.get(field)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ValueError(f'观测缺少有效的 {field}')
        row[field] = value.strip()
    if row.get('run_kind') not in RUN_MODES:
        raise ValueError('观测必须显式声明 run_kind')
    if row.get('quality') not in {'observed', 'estimated', 'proxy', 'missing', 'legacy'}:
        raise ValueError('观测必须显式声明有效质量')
    row.setdefault('value', None)
    if row['value'] is None:
        if row['quality'] != 'missing':
            raise ValueError('非缺测观测必须有有限数值')
    elif not finite_number(row['value']) or row['quality'] == 'missing':
        raise ValueError('观测数值与质量不一致')
    start, end = business_date(row.get('period_start')), business_date(row.get('period_end'))
    if not start or not end or end <= start or row.get('period_end_exclusive', True) is not True:
        raise ValueError('观测需要有效的排他结束周期')
    if (start.year >= 9999 or row['period_start'] not in (start, start.isoformat())
            or row['period_end'] not in (end, end.isoformat())):
        raise ValueError('观测周期必须为纯日期且在支持范围内')
    frequency = row.get('frequency')
    if frequency == 'monthly':
        expected = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        complete = start.day == 1 and end == expected
    elif frequency == 'annual':
        complete = start.month == start.day == 1 and end == date(start.year + 1, 1, 1)
    elif frequency == 'daily':
        complete = end == start + timedelta(days=1)
    else:
        raise ValueError('观测频率必须为 daily、monthly 或 annual')
    if not complete:
        raise ValueError('观测周期必须是完整自然日、月或年')
    if not isinstance(row.get('product', ''), str):
        raise ValueError('观测 product 必须为字符串')
    row.update(period_start=start.isoformat(), period_end=end.isoformat(),
               period_end_exclusive=True, product=row.get('product', '').strip())
    identity = row.get('id')
    if identity is None:
        identity = fingerprint({k: row[k] for k in ('source', 'indicator', 'region', 'period_start',
                                'frequency', 'product', 'dataset_version')})
    if not isinstance(identity, str) or not identity.strip() or len(identity) > 64:
        raise ValueError('观测 id 必须为1～64字符')
    row['id'] = identity
    return json_safe(row)


def calendar_series(records, days=30, end_date=None):
    start, end = time_window(days, end_date)
    lookup = {business_date(r["date"]).isoformat(): r["risk_score"] for r in records if valid_risk_record(r)}
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]
    return dates, [lookup.get(d) for d in dates]
