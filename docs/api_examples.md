# API 接口文档

缅甸地缘风险智能分析原型系统 - RESTful API 参考

Base URL: `http://localhost:5000`

---

## 1. 健康检查

**GET** `/health`

### 响应示例

```json
{
  "status": "ok",
  "service": "缅甸地缘风险分析系统",
  "timestamp": "2026-06-15T12:00:00.000000"
}
```

---

## 2. 文本分析接口

**POST** `/api/analyze`

对缅甸相关新闻进行结构化分析（NER + 情感 + LLM + 风险评分）。

### 请求体

```json
{
  "text": "缅甸军方与克钦独立军在掸邦北部发生武装冲突，冲突持续约4小时，军方出动空中力量进行轰炸，导致多个村庄平民被迫转移。",
  "instruction": "请重点分析对中缅油气管道安全的影响"
}
```

> `instruction` 为可选字段，用于自定义分析指令。不传时使用默认提示词模板。

### 响应示例

```json
{
  "success": true,
  "data": {
    "entities": {
      "locations": ["缅甸", "掸邦", "克钦"],
      "organizations": ["克钦独立军"],
      "persons": [],
      "events": ["冲突", "武装", "空袭"]
    },
    "sentiment": {
      "sentiment_score": 0.15,
      "risk_score": 0.85,
      "risk_level": "high"
    },
    "llm_analysis": {
      "event_type": "军事冲突",
      "severity": 4,
      "china_myanmar_impact": "冲突威胁中缅经济走廊项目安全，可能影响管道运营",
      "risk_warning": "掸邦北部冲突升级将直接威胁中国在缅资产和人员安全",
      "key_entities": ["缅甸军方", "克钦独立军", "掸邦"],
      "key_locations": ["掸邦北部", "抹谷镇"],
      "summary": "缅军与克钦独立军武装冲突升级，出动空军轰炸，平民大规模转移",
      "sentiment": "negative"
    },
    "risk_score": {
      "risk_score": 72.5,
      "risk_level": "高风险",
      "gdelt_used": true,
      "indicator_scores": {
        "conflict_frequency": {"value": 1.0, "weight": 0.3, "contribution": 0.3},
        "sentiment_avg": {"value": 0.85, "weight": 0.25, "contribution": 0.2125},
        "nightlight_change": {"value": 0.0, "weight": 0.2, "contribution": 0.0},
        "refugee_change": {"value": 0.0, "weight": 0.15, "contribution": 0.0},
        "event_severity": {"value": 0.8, "weight": 0.1, "contribution": 0.08}
      }
    },
    "gdelt_metrics": {
      "article_count": 87,
      "conflict_count": 23,
      "conflict_frequency": 0.2644,
      "avg_tone_risk": 0.68,
      "avg_severity": 0.54,
      "max_severity": 0.9,
      "event_summary": {"conflict": 23, "unrest": 15, "diplomacy": 8}
    },
    "warnings": []
  }
}
```

### 错误响应

```json
{
  "success": false,
  "error": "缺少 'text' 字段"
}
```

---

## 3. GDELT 事件数据接口

**GET** `/api/gdelt?days=7`

从 GDELT (Global Database of Events, Language, and Tone) 全球事件数据库查询缅甸相关地缘政治事件。

> GDELT 是免费的全球新闻事件数据库，包含事件编码、情感分数、地理位置等结构化信息，用于增强冲突频次和事件严重程度的评估。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| days | int  | 7      | 查询最近多少天的数据 |

### 响应示例

```json
{
  "success": true,
  "data": {
    "article_count": 87,
    "conflict_count": 23,
    "conflict_frequency": 0.2644,
    "avg_tone_risk": 0.68,
    "avg_severity": 0.54,
    "max_severity": 0.9,
    "event_summary": {
      "conflict": 23,
      "unrest": 15,
      "diplomacy": 8
    },
    "top_locations": [
      {"name": "Myanmar", "count": 87},
      {"name": "Yangon", "count": 32},
      {"name": "Shan State", "count": 18}
    ]
  }
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `article_count` | GDELT 查询返回的缅甸相关文章总数 |
| `conflict_count` | 包含冲突事件（CAMEO code 17-22）的文章数 |
| `conflict_frequency` | 冲突文章占比 (0~1) |
| `avg_tone_risk` | 平均情感风险分 (0~1，GDELT tone 归一化) |
| `avg_severity` | 平均事件严重程度 (0~1) |
| `max_severity` | 最大事件严重程度 (0~1) |
| `event_summary` | 事件分类统计（冲突/动荡/外交） |
| `top_locations` | 出现最多的地点 (top 10) |

---

## 4. 风险地图接口

**GET** `/api/map?days=7`

返回缅甸省级风险地图 HTML（folium 生成，GADM 省界 + 6 档色阶），可直接嵌入 iframe。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| days | int  | 7      | 查询最近多少天的数据 |
| mode | string | "choropleth" | `choropleth` 分级填色 / `hybrid` 风险圆点+省界描边（悬停高亮） |

### 响应

Content-Type: `text/html`

返回完整 HTML 字符串，包含 folium 交互式地图。颜色映射：红（高风险）→ 黄（中风险）→ 绿（低风险）。

### 前端嵌入示例

```html
<iframe src="/api/map?days=7" width="100%" height="600px" frameborder="0"></iframe>
```

---

## 4b. 事件密度（KDE）地图接口

**GET** `/api/map/events?days=7`

基于 GDELT 事件经纬度的**严重度加权核密度估计**，叠加 GADM 4.1 真实国界/省界。
数据来自事件累积库（逐轮增量积累、GlobalEventID 去重、180 天保留），
支持 7~90 天窗口；多信源（≥2 家报道）互证事件权重 ×1.25；
密度面用 matplotlib 栅格 PNG 叠加渲染（任意缩放平滑无伪影）。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| days | int  | 7      | 统计窗口（按事件日期过滤，随累积库增长可达 90） |

### 响应

Content-Type: `text/html`

返回完整 HTML（folium）：暗色底图 + 国界/省界 + 密度栅格叠加 + 峰值标注。
累积库为空时自动触发首次全量拉取（数分钟）；有效定位事件不足时
返回带降级提示的边界地图（非错误状态码）。

### 算法说明

- 权重：事件严重度（CAMEO 根码映射，冲突 ≥0.7 / 动荡 ≥0.4，与风险指标口径一致）
- 带宽：scipy Scott 法则（随样本量自适应）
- 掩膜：国境外密度置零；网格约 120×95，可在 `config.yaml` 的 `kde` 段调整

---

## 5. 趋势分析接口

**GET** `/api/trend?days=30&chart=true`

返回历史风险分序列、趋势分析和预测数据。

### 参数

| 参数  | 类型   | 默认值 | 说明 |
|-------|--------|--------|------|
| days  | int    | 30     | 查询最近多少天 |
| chart | string | "true" | 是否包含图表数据 |

### 响应示例

```json
{
  "success": true,
  "data": {
    "dates": ["2026-05-16", "2026-05-17", "2026-05-18", "..."],
    "history": [45.2, 48.1, 52.3, "..."],
    "forecast": [53.1, 54.2, 55.0, 55.8, 56.5, 57.1, 57.6],
    "trend_analysis": {
      "moving_average": [46.5, 47.8, 49.1, "..."],
      "regression": {
        "slope": 0.15,
        "intercept": 44.2,
        "r_squared": 0.78,
        "trend": "上升"
      },
      "trend": "上升",
      "latest_score": 52.3,
      "avg_score": 48.7,
      "data_points": 30
    },
    "anomalies": [
      {
        "index": 12,
        "value": 78.5,
        "z_score": 2.8,
        "type": "peak"
      }
    ],
    "chart_data": {
      "type": "line",
      "title": "缅甸地缘风险趋势",
      "xAxis": ["2026-05-16", "..."],
      "series": [
        {"name": "风险分", "data": [45.2, "..."]},
        {"name": "移动平均", "data": [46.5, "..."]}
      ]
    }
  }
}
```

### 趋势判断标准

- **上升**：斜率 > 0.005（风险分逐日上升）
- **下降**：斜率 < -0.005（风险分逐日下降）
- **平稳**：斜率在 ±0.005 之间

---

## 6. 前端页面路由

| 路径     | 页面       | 说明 |
|----------|------------|------|
| `/`      | chat.html  | 对话分析：输入文本 → 结构化分析结果 |
| `/map`   | map.html   | 风险地图：双模式（省界分级填色 / 事件密度 KDE） |
| `/trend` | trend.html | 趋势预测：ECharts 折线图 |
| `/dashboard` | dashboard.html | 综合态势：预警、位势、网络和多源融合 |

---

## 7. 其他业务接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chain` | 分步链式推理，参数 `chain_depth` 为 1-4 |
| GET | `/api/history` | 查询历史事件 |
| GET | `/api/multimodal` | 夜光、冲突与情感的时空对齐 |
| GET | `/api/geo_potential` | 地缘位势与空间自相关 |
| GET | `/api/diagnostic` | 风险变化归因 |
| GET | `/api/alert` | 当前预警与阈值线 |
| POST | `/api/alert/acknowledge` | 确认预警 |
| GET/POST | `/api/scheduler` | 调度状态或手动触发任务 |
| GET | `/api/sources/health` | 数据源健康状态（成功率/降级监控），响应示例见下 |
| GET | `/api/kg/query` | 查询知识图谱 |
| POST | `/api/kg/seed` | 写入知识图谱种子数据 |
| GET | `/api/network` | 关系网络分析 |
| GET | `/api/report` | 导出 HTML 或 DOCX 报告 |

`/api/analyze` 的 `data.warnings` 会列出 LLM、GDELT、夜光、经济、Neo4j、
预警或诊断模块的降级原因。可选模块失败不会把成功的规则分析改成 HTTP 500。

### `/api/sources/health` 响应示例

```json
{
  "success": true,
  "data": {
    "缅甸缅华网": {
      "status": "healthy",
      "success_rate": 1.0,
      "recent_attempts": 5,
      "recent_success": 5,
      "last_count": 6,
      "last_success": "2026-08-05T11:27:10",
      "last_error": null,
      "records": [
        {"time": "2026-08-05T11:27:10", "ok": true, "count": 6, "error": null}
      ]
    },
    "The Irrawaddy": {
      "status": "dead",
      "success_rate": 0.0,
      "recent_attempts": 3,
      "recent_success": 0,
      "last_count": 0,
      "last_success": null,
      "last_error": "Irrawaddy 列表页请求失败: https://www.irrawaddy.com/news/burma",
      "records": []
    }
  }
}
```

`status` 取值：`healthy`（成功率≥70%且最近一次成功）/ `degraded`（时好时坏）/
`dead`（最近 3 次全部失败）。每源滚动保留最近 20 次记录，持久化到
`DATA_ROOT/processed/source_health.json`，重启不丢失。

---

## 8. 快速测试命令

```bash
# 健康检查
curl http://localhost:5000/health

# 文本分析
curl -X POST http://localhost:5000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"text": "缅甸军方与克钦独立军在掸邦北部发生武装冲突"}'

# 趋势数据
curl "http://localhost:5000/api/trend?days=30"

# 地图 HTML
curl "http://localhost:5000/api/map?days=7" -o map.html

# GDELT 事件数据
curl "http://localhost:5000/api/gdelt?days=7"
```
