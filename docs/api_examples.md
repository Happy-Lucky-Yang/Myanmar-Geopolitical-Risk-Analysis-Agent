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
      "risk_level": "高风险",
      "keywords": ["冲突", "轰炸", "转移"]
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
      "indicator_scores": {
        "conflict_frequency": {"value": 1.0, "weight": 0.3, "contribution": 0.3},
        "sentiment_avg": {"value": 0.85, "weight": 0.2, "contribution": 0.17},
        "nightlight_change": {"value": 0.0, "weight": 0.2, "contribution": 0.0},
        "refugee_change": {"value": 0.0, "weight": 0.15, "contribution": 0.0},
        "event_severity": {"value": 0.8, "weight": 0.15, "contribution": 0.12}
      }
    }
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

## 3. 风险地图接口

**GET** `/api/map?days=7`

返回缅甸风险热力地图 HTML（folium 生成），可直接嵌入 iframe。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| days | int  | 7      | 查询最近多少天的数据 |

### 响应

Content-Type: `text/html`

返回完整 HTML 字符串，包含 folium 交互式地图。颜色映射：红（高风险）→ 黄（中风险）→ 绿（低风险）。

### 前端嵌入示例

```html
<iframe src="/api/map?days=7" width="100%" height="600px" frameborder="0"></iframe>
```

---

## 4. 趋势分析接口

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

## 5. 前端页面路由

| 路径     | 页面       | 说明 |
|----------|------------|------|
| `/`      | chat.html  | 对话分析：输入文本 → 结构化分析结果 |
| `/map`   | map.html   | 风险地图：folium 热力地图 |
| `/trend` | trend.html | 趋势预测：ECharts 折线图 |

---

## 6. 快速测试命令

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
```
