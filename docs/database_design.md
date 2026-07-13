# 数据库设计说明书

## 1. 概述

本系统采用**轻量级文件存储**（JSONL/JSON）+ **可选图数据库**（Neo4j）的混合存储方案。

---

## 2. JSONL 风险评分记录

**文件**: `data/raw/risk_history.jsonl`

每条记录格式：
```json
{
  "date": "2026-01-15",
  "risk_score": 65.3,
  "risk_level": "中风险",
  "details": {
    "conflict_frequency": 0.8,
    "sentiment_avg": 0.65,
    "nightlight_change": 0.45,
    "refugee_change": 0.72,
    "event_severity": 0.6
  }
}
```

**字段说明**：
| 字段 | 类型 | 说明 |
|------|------|------|
| date | string | 日期 (YYYY-MM-DD) |
| risk_score | float | 综合风险分 (0-100) |
| risk_level | string | 风险等级: 高/中/低 |
| details | object | 各指标原始值 (0-1) |

---

## 3. GDELT 数据缓存

**文件**: `data/raw/gdelt_news.jsonl`

```json
{
  "title": "Myanmar conflict escalates...",
  "url": "https://...",
  "date": "2026-01-15",
  "source": "GDELT",
  "tone": -3.5,
  "themes": ["CONFLICT", "MILITARY"],
  "locations": [{"name": "Shan State", "lat": 21.5, "lon": 98.0}],
  "language": "en"
}
```

---

## 4. 夜间灯光遥感缓存

**文件**: `data/raw/nightlight_cache.json`

```json
{
  "nightlight_change": 0.45,
  "source": "worldbank",
  "indicators_used": ["electricity_access", "transmission_loss"],
  "raw_values": {"electricity_access": 68.5, "transmission_loss": 15.2},
  "data_year": 2024,
  "monthly_series": [{"month": "2025-01", "value": 0.43}, ...],
  "fetched_at": "2026-01-15T10:30:00",
  "data_quality": "估算"
}
```

---

## 5. 经济统计数据存储

**文件**: `data/raw/economic_indicators.json`

```json
{
  "gdp_growth": 2.1,
  "gdp_growth_norm": 0.484,
  "gdp_per_capita": 1180.5,
  "inflation": 18.3,
  "inflation_norm": 0.334,
  "trade_pct_gdp": 55.2,
  "trade_change_norm": 0.552,
  "refugee_change": 0.75,
  "source": "worldbank",
  "data_year": 2024,
  "fetched_at": "2026-01-15T10:30:00",
  "data_quality": "官方"
}
```

---

## 6. 历史事件数据集

**文件**: `data/raw/historical_events.json`

```json
[
  {
    "date": "2021-02-01",
    "event_type": "政变",
    "actors": ["缅甸国防军", "敏昂莱"],
    "location": "内比都",
    "severity": 5,
    "description": "缅甸军方发动政变",
    "source": "全球新闻"
  }
]
```

---

## 7. 预警历史

**文件**: `data/raw/alerts.json`

```json
[
  {
    "id": "alert_20260115103000",
    "level": "orange",
    "label": "橙色预警",
    "color": "#d29922",
    "risk_score": 65.3,
    "triggered_at": "2026-01-15T10:30:00",
    "acknowledged": false
  }
]
```

---

## 8. Neo4j 图数据库 Schema

### 节点类型

| 标签 | 属性 | 说明 |
|------|------|------|
| Country | name, region | 国家 |
| Organization | name, aliases | 组织 |
| Person | name, role | 人物 |
| Location | name, type | 地点 |
| NewsEvent | name, date, source | 新闻事件 |
| EventType | name | 事件类型 |

### 关系类型

| 关系 | 方向 | 属性 | 说明 |
|------|------|------|------|
| CONFLICT_WITH | A→B | since | 冲突关系 |
| COOPERATE_WITH | A→B | | 合作关系 |
| MEMBER_OF | A→B | | 成员关系 |
| SANCTIONS | A→B | since | 制裁关系 |
| OPERATES_IN | A→B | | 活动区域 |
| MENTIONS_LOCATION | A→B | | 提及地点 |
| MENTIONS_ORGANIZATION | A→B | | 提及组织 |
| MENTIONS_PERSON | A→B | | 提及人物 |
| INVESTS_IN | A→B | | 投资关系 |
| TRIGGERED | A→B | | 触发关系 |
| CAUSES | A→B | | 因果关系 |

---

## 9. 数据更新策略

| 数据类型 | 更新频率 | 缓存有效期 | 过期处理 |
|----------|----------|-----------|---------|
| 新闻文本 | 4小时 | 不自动删除 | 滚动保留最近90天 |
| GDELT 事件 | 6小时 | 7天 | 自动覆盖 |
| 夜光遥感 | 7天 | 7天 | 重新获取 |
| 经济指标 | 30天 | 30天 | 重新获取 |
| 风险评分 | 12小时 | 永久 | 不删除 |
| 预警记录 | 实时 | 永久 | 保留最近100条 |

---

## 10. 数据可信度标记规范

| 标记 | 含义 | 示例 |
|------|------|------|
| 官方 | API/权威数据源直接获取 | World Bank GDP 数据 |
| 估算 | 基于有限数据模型推断 | 夜光代理指标、难民估算 |
| 线性插补 | 年度数据按月度线性插值 | 月度经济指标序列 |
| 合成 | 无真实数据时生成 | 降级序列数据 |
