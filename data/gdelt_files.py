"""
data.gdelt_files - GDELT 原始数据文件直连通道（不限流）

DOC 2.0 API 按 IP 限流（每 5 秒 1 请求），高峰期可能全局拒绝；
GDELT 每 15 分钟发布的原始事件文件（data.gdeltproject.org/gdeltv2/）
下载不限流，是学术界标准的批量接入方式。

策略：从最新时刻向前枚举 15 分钟粒度的更新文件，下载并流式过滤
缅甸（FIPS=BM）事件行，达到目标条数或文件上限即停止，
最后聚合成与 compute_gdelt_risk_metrics 同构的风险指标。

事件文件为制表符分隔 CSV（61 列），关键列（0 基索引）：
    1  SQLDATE(YYYYMMDD)        26 EventCode(CAMEO)
    28 EventRootCode             29 QuadClass(1~4)
    34 AvgTone                   52 ActionGeo_FullName
    53 ActionGeo_CountryCode     60 SOURCEURL
"""
import io
import csv
import zipfile
import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

GDELT_FILES_BASE = "http://data.gdeltproject.org/gdeltv2/"
MYANMAR_FIPS = "BM"

# events export CSV 列索引（0 基）
COL_SQLDATE = 1
COL_EVENTCODE = 26
COL_EVENTROOTCODE = 28
COL_QUADCLASS = 29
COL_AVGTONE = 34
COL_ACTIONGEO_FULLNAME = 52
COL_ACTIONGEO_COUNTRY = 53
COL_ACTIONGEO_LAT = 56
COL_ACTIONGEO_LON = 57
COL_SOURCEURL = 60
_MIN_COLS = 61

# 缅甸扩展包围盒（坐标合法性校验，剔除 GDELT 地理编码噪声）
_BBOX_LAT = (8.0, 30.0)
_BBOX_LON = (90.0, 103.0)

# CAMEO 根事件码 → (分类, 严重度 0~1)，参照 GDELT 官方编码手册
ROOT_INFO = {
    "01": ("diplomacy", 0.05), "02": ("diplomacy", 0.08),
    "03": ("diplomacy", 0.08), "04": ("diplomacy", 0.10),
    "05": ("diplomacy", 0.12), "06": ("diplomacy", 0.15),
    "07": ("diplomacy", 0.15), "08": ("diplomacy", 0.18),
    "09": ("diplomacy", 0.20), "10": ("diplomacy", 0.22),
    "11": ("diplomacy", 0.25), "12": ("diplomacy", 0.28),
    "13": ("unrest", 0.35), "14": ("unrest", 0.45),
    "15": ("unrest", 0.55), "16": ("unrest", 0.65),
    "17": ("conflict", 0.70), "18": ("conflict", 0.78),
    "19": ("conflict", 0.88), "20": ("conflict", 0.95),
}


def _get_latest_batch_ts(base_url: str = None) -> Optional[datetime]:
    """从 lastupdate.txt 解析最新已发布批次的时间戳（权威来源）"""
    base = (base_url or GDELT_FILES_BASE).rstrip("/") + "/"
    try:
        resp = requests.get(base + "lastupdate.txt", timeout=20)
        resp.raise_for_status()
        # 首行: <size> <md5> <url>，url 形如 .../20260806041500.export.CSV.zip
        url = resp.text.strip().splitlines()[0].split()[-1]
        ts = url.rsplit("/", 1)[-1].split(".")[0]
        return datetime.strptime(ts, "%Y%m%d%H%M%S")
    except Exception as e:
        logger.warning(f"[GDELT-CSV] lastupdate.txt 解析失败: {e}")
        return None


def enumerate_batch_urls(end_dt: datetime = None, count: int = 672,
                         base_url: str = None) -> List[str]:
    """
    从最新已发布批次向前枚举 15 分钟粒度的事件文件 URL（新→旧排序）

    优先用 lastupdate.txt 定位最新批次；失败时回退到
    "当前时刻回退 20 分钟再对齐"的估算（个别文件 404 由调用方跳过）。

    :param end_dt: 结束时刻（默认读 lastupdate.txt，再退化为当前 UTC）
    :param count: 枚举文件数（覆盖 count*15 分钟窗口，672 ≈ 7 天）
    :param base_url: 文件服务器基址
    :return: URL 列表
    """
    base = (base_url or GDELT_FILES_BASE).rstrip("/") + "/"

    t = end_dt or _get_latest_batch_ts(base)
    if t is None:
        t = (datetime.utcnow() - timedelta(minutes=20)).replace(
            second=0, microsecond=0)
        t = t.replace(minute=(t.minute // 15) * 15)

    urls = []
    for _ in range(count):
        urls.append(f"{base}{t.strftime('%Y%m%d%H%M%S')}.export.CSV.zip")
        t -= timedelta(minutes=15)
    return urls


def _parse_csv_rows(csv_text: str, country: str) -> List[Dict]:
    """流式解析事件 CSV，仅保留指定国家（ActionGeo）的行"""
    events = []
    reader = csv.reader(io.StringIO(csv_text), delimiter="\t")
    for row in reader:
        if len(row) < _MIN_COLS or row[COL_ACTIONGEO_COUNTRY] != country:
            continue
        try:
            tone = float(row[COL_AVGTONE]) if row[COL_AVGTONE] else 0.0
        except ValueError:
            tone = 0.0
        # 经纬度（可能为空或地理编码错误，越界置 None 供下游过滤）
        lat = lon = None
        try:
            _lat = float(row[COL_ACTIONGEO_LAT])
            _lon = float(row[COL_ACTIONGEO_LON])
            if (_BBOX_LAT[0] <= _lat <= _BBOX_LAT[1]
                    and _BBOX_LON[0] <= _lon <= _BBOX_LON[1]):
                lat, lon = _lat, _lon
        except (ValueError, IndexError):
            pass
        events.append({
            "date": row[COL_SQLDATE],
            "event_code": row[COL_EVENTCODE],
            "root_code": row[COL_EVENTROOTCODE],
            "quad_class": row[COL_QUADCLASS],
            "avg_tone": tone,
            "location": row[COL_ACTIONGEO_FULLNAME],
            "lat": lat,
            "lon": lon,
            "source_url": row[COL_SOURCEURL],
        })
    return events


def event_severity_weight(ev: Dict) -> float:
    """
    事件严重度权重（0~1），指标聚合与 KDE 密度估计共用，保证口径一致

    规则：QuadClass 4（实质性冲突）或冲突类根码 → ≥0.7；
    QuadClass 3（口头冲突/动荡）→ ≥0.4；其余按根码映射。
    """
    root = ev.get("root_code", "")[:2]
    category, severity = ROOT_INFO.get(root, ("diplomacy", 0.1))
    if ev.get("quad_class") == "4" or category == "conflict":
        return max(severity, 0.7)
    if ev.get("quad_class") == "3":
        return max(severity, 0.4)
    return severity


def event_category(ev: Dict) -> str:
    """事件分类（conflict/unrest/diplomacy），与严重度规则联动"""
    root = ev.get("root_code", "")[:2]
    category, _ = ROOT_INFO.get(root, ("diplomacy", 0.1))
    if ev.get("quad_class") == "4" or category == "conflict":
        return "conflict"
    if ev.get("quad_class") == "3":
        return "unrest"
    return category


def fetch_myanmar_events(max_files: int = 672, target_events: int = 200,
                         timeout: int = 120, base_url: str = None,
                         country: str = MYANMAR_FIPS) -> List[Dict]:
    """
    下载最近的 15 分钟事件文件并过滤缅甸事件

    单文件仅 ~50KB，672 个文件（≈7 天窗口）总带宽约 35MB；
    缅甸事件稀疏（日均数十条），需多日窗口才能积累足够样本。

    :param max_files: 最多下载的文件数（带宽上限保护）
    :param target_events: 收集到足够事件后提前停止（新事件优先）
    :param timeout: 单文件下载超时（秒）
    :param base_url: 文件服务器基址
    :param country: FIPS 国家码（默认 BM=缅甸）
    :return: 缅甸事件字典列表（新→旧顺序）
    """
    events = []
    urls = enumerate_batch_urls(count=max_files, base_url=base_url)

    for i, url in enumerate(urls):
        if len(events) >= target_events:
            logger.info(f"[GDELT-CSV] 已达目标事件数 {target_events}，提前停止")
            break
        try:
            resp = requests.get(url, timeout=(15, timeout))
            if resp.status_code == 404:
                continue  # 该批次尚未发布，继续向前
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                name = zf.namelist()[0]
                text = zf.read(name).decode("utf-8", errors="replace")

            batch = _parse_csv_rows(text, country)
            events.extend(batch)
            logger.info(
                f"[GDELT-CSV] 文件 {i+1}/{len(urls)}: "
                f"缅甸事件 +{len(batch)}（累计 {len(events)}）"
            )
        except Exception as e:
            logger.warning(f"[GDELT-CSV] 文件下载/解析失败 {url}: {e}")
            continue

    return events


def compute_metrics_from_events(events: List[Dict]) -> Dict:
    """
    由事件行聚合风险指标（与 compute_gdelt_risk_metrics 输出同构）

    :param events: fetch_myanmar_events 的返回值
    :return: 指标字典（article_count/conflict_frequency/avg_tone_risk 等）
    """
    if not events:
        return {
            "article_count": 0,
            "conflict_count": 0,
            "conflict_frequency": 0.0,
            "avg_tone_risk": 0.5,
            "avg_severity": 0.0,
            "max_severity": 0.0,
            "event_summary": {},
            "top_locations": [],
            "data_channel": "gdelt_csv",
        }

    n = len(events)
    conflict_count = 0
    tone_risks = []
    severities = []
    event_counts = {}
    location_counts = {}

    for ev in events:
        severity = event_severity_weight(ev)
        category = event_category(ev)

        if category == "conflict":
            conflict_count += 1

        severities.append(severity)
        event_counts[category] = event_counts.get(category, 0) + 1

        # tone 风险映射：AvgTone 典型范围 -10~10，越负风险越高
        tone_risks.append(max(0.0, min(1.0, 0.5 - ev.get("avg_tone", 0.0) / 20.0)))

        loc = ev.get("location", "")
        if loc:
            location_counts[loc] = location_counts.get(loc, 0) + 1

    top_locations = sorted(
        location_counts.items(), key=lambda x: x[1], reverse=True
    )[:10]

    return {
        "article_count": n,
        "conflict_count": conflict_count,
        "conflict_frequency": round(conflict_count / max(n, 1), 4),
        "avg_tone_risk": round(sum(tone_risks) / len(tone_risks), 4),
        "avg_severity": round(sum(severities) / len(severities), 4),
        "max_severity": round(max(severities), 4) if severities else 0.0,
        "event_summary": event_counts,
        "top_locations": [{"name": k, "count": v} for k, v in top_locations],
        "data_channel": "gdelt_csv",
    }
