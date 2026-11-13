# API 接口文档

缅甸地缘风险智能分析原型系统 - RESTful API 参考

Base URL: `http://localhost:5000`（仅本地示例；共享访问须 HTTPS）。以下数值为结构示意，不是当前实测结果。

## 通用数据与权限契约
- 正式查询使用 `days`（1～3660）、`end_date`（含当天的业务日）、`region`（默认MMR）、`source`（空为全部）。日期按 Asia/Yangon 连续日历计算，缺测null，真实0保留。
- 地图、趋势、多模态、网络和报告按参数/数据修订隔离；传入过期 `revision` 或读取中数据变化返回409。地图响应头为 `X-Data-Revision`；报告输出 `X-Snapshot-ID`。不要把旧曲线或旧报告链接当新请求结果。
- 来源子集尚未重算综合日风险：趋势及报告综合风险留空，诊断/预警不支持单来源而返回400；不把全国评分套给来源或省份。历史事件接口单独使用 `year/event_type/severity_min`，不是日风险数据域。
- 共享模式先 `GET /api/session` 获取 CSRF token，再带 Cookie 和 `X-CSRF-Token` 调用 `POST /api/login`；登录后使用返回的新 token。reader只读，analyst分析/确认预警，admin管理采集/种子。未登录401，权限/CSRF失败403，限流429。
- 共享模式 `/api/analyze`、`/api/chain`、采集提交返回202及 `data.job_id/status`，通过 `GET /api/jobs/<job_id>` 查询。可带 `Idempotency-Key` 防重复提交。任务重试由worker处理；手工任务仅本人可读，admin不绕过此限制。
- `GET /api/analyses/<run_id>` 获取本人或主动共享的分析；本人用 `POST /api/analyses/<run_id>/share` 和 `{"shared":true}` 共享，可用false撤回。手工分析不写全国日指标，也不触发正式预警。

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
      "risk_score": 93.18,
      "risk_level": "高风险",
      "gdelt_used": false,
      "run_kind": "manual",
      "algorithm_version": "risk-v2",
      "scope": "仅用户输入的文本，不代表全国当日风险",
      "indicator_coverage": 0.55,
      "indicator_scores": {
        "conflict_frequency": {"value": 1.0, "weight": 0.5454545, "contribution": 0.5454545},
        "sentiment_avg": {"value": 0.85, "weight": 0.4545455, "contribution": 0.3863636},
        "nightlight_change": {"value": null, "weight": 0, "contribution": null},
        "refugee_change": {"value": null, "weight": 0, "contribution": null},
        "event_severity": {"value": null, "weight": 0, "contribution": null}
      }
    },
    "gdelt_metrics": null,
    "alert": null,
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

只读本地/数据库已经持久化的 GDELT 事件记录，不同步调用远程采集。支持通用日历、区域和来源筛选。

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
    "event_count": 87,
    "article_count": null,
    "classified_count": 87,
    "algorithm_version": "event-metrics-v2",
    "conflict_count": 23,
    "conflict_frequency": 0.2644,
    "avg_tone_risk": 0.68,
    "avg_severity": 0.41,
    "max_severity": 0.88,
    "event_summary": {
      "conflict": 23,
      "unrest": 15,
      "diplomacy": 49
    },
    "top_locations": [
      {"name": "Myanmar", "count": 37},
      {"name": "Yangon", "count": 32},
      {"name": "Shan State", "count": 18}
    ]
  }
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `event_count` | 按来源事件ID去重的事件记录数，不是独立报道数或已核验现实事件数 |
| `article_count` | 此事件接口为null，不用事件数量冒充文章数量 |
| `classified_count` | 可识别 CAMEO 根码的记录数，未知类别不纳入占比分母 |
| `conflict_count` | 冲突类（CAMEO 根码17～20）记录数；无可分类记录时null |
| `conflict_frequency` | 冲突类记录 / 可分类记录 (0～1)；无分母时null |
| `avg_tone_risk` | 平均情感风险分 (0~1，GDELT tone 归一化) |
| `avg_severity` | 平均事件严重程度 (0~1) |
| `max_severity` | 最大事件严重程度 (0~1) |
| `event_summary` | 事件分类统计（冲突/动荡/外交） |
| `top_locations` | 出现最多的地点 (top 10) |

---

## 4. 风险地图接口

**GET** `/api/map?days=7`

返回省级事件规则烈度地图 HTML（Folium + GADM），可嵌入同源 iframe。按真实事件发生地归属，未定位单列，无数据省份灰显；不是全国日风险乘省份系数。

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

## 4a. 统一地图接口（图层面板版）

**GET** `/api/map/unified?days=7`

单张地图内含全部可叠加图层，由前端自定义图层面板控制：

| 图层 | 说明 | 默认 |
|------|------|------|
| 分级填色 | GADM 省界按风险分染色 | 关（与圆点互斥） |
| 圆点+光晕 | 风险圆点 + 热力风格荧光光晕 | 开（与填色互斥） |
| 事件密度 KDE | 栅格密度叠加，透明度可调 | 开 |
| 省界/国界 | 边界描边，悬停高亮 | 开 |

实现要点：图层对象经注入脚本注册到 `window._mmLayers`（含 iframe 跨窗口
传递），前端面板通过 `addTo/removeLayer/setOpacity` 控制；注入脚本必须为
纯 JS（folium script 段已包在 `<script>` 块内，嵌套标签会提前闭合外层块）。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| days | int  | 7      | 统计窗口 |

### 响应

Content-Type: `text/html`；带5分钟 HTML 缓存，缓存键包含完整筛选及数据修订。数据变化即失效，不等TTL结束。返回 `X-Data-Revision`，空窗口给原因，不自动采集。

---

## 4b. 事件密度（KDE）地图接口

**GET** `/api/map/events?days=7`

基于事件经纬度的加权核密度估计，叠加真实国界/省界。
数据来自事件累积库（增量积累、按来源事件ID去重，不自动裁剪历史），支持通用日历窗口。多来源报道只是证据数量信号，不等于已独立核验。密度面以 matplotlib 栅格 PNG 叠加，缩放不能增加原始数据分辨率。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| days | int  | 7      | 连续日历天数，实际覆盖随持久化数据而定 |

### 响应

Content-Type: `text/html`

返回完整 HTML（folium）：暗色底图 + 国界/省界 + 密度栅格叠加 + 峰值标注。
累积库为空或有效定位事件不足时返回带原因的边界地图，不联网补数据。采集须另行显式提交。

### 算法说明

- 权重：事件规则烈度（CAMEO 根码映射，冲突0.70～0.95、动荡0.35～0.65），不是全国日风险。
- 带宽：本地等面积投影，固定公里带宽；跨时间比较共同带宽及色标，按天归一。
- 输出绝对加权事件密度和相对密度；相对峰值归一值仅用于同窗口位置对比。
- 国界掩膜和栅格参数见 `config.yaml` 的 `kde` 段。

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
    "dates": ["2026-05-16", "2026-05-17", "2026-05-18"],
    "history": [0, null, 52.3],
    "forecast": [],
    "forecast_meta": {
      "status": "insufficient", "algorithm_version": "trend-v2",
      "reason": "需至少14个有效日、覆盖率≥80%，且截止日有观测",
      "backtest": null, "interval": null, "confidence": "未验收"
    },
    "metadata": {
      "requested_start": "2026-05-16", "requested_end": "2026-05-18",
      "valid_days": 2, "requested_days": 3, "coverage": 0.6667,
      "timezone": "Asia/Yangon", "data_status": "partial"
    },
    "filters": {"days": 3, "end_date": "2026-05-18", "region": "MMR", "source": null, "domain": "observed"},
    "revision": "示例修订号",
    "report_available": true,
    "anomalies": []
  }
}
```

上例省略了 `trend_analysis/threshold_lines/event_markers`，相当于 `days=3&end_date=2026-05-18&chart=false`。实际日期数组长度等于请求日数，缺测不删点。

预测使用末值/EWMA/真实日期线性模型；至少56有效日且完成4个七日滚动窗口才返回MAE/RMSE，回测不足保留末值基线。`r_squared` 只描述拟合，不是可靠性；经验残差区间不保证覆盖概率。异常使用历史中位数/MAD，字段为 `deviation/method/date/index/value/type`，不再叫Z-score。

`domain=legacy` 仅显式历史对照，不给正式预测、预警或报告。来源筛选时综合评分留空，不从子集沿用全量评分。

### 趋势判断标准

- **上升**：斜率 > 0.005（风险分逐日上升）
- **下降**：斜率 < -0.005（风险分逐日下降）
- **平稳**：斜率在 ±0.005 之间

---

## 6. 前端页面路由

### 主页面

| 路径 | 页面 | 说明 |
|------|------|------|
| `/` | chat.html | 对话分析：输入文本 → 结构化分析结果 |
| `/map` | map.html | 风险地图：双模式（省界分级填色 / 事件密度 KDE） |
| `/trend` | trend.html | 趋势预测：ECharts 折线图 |
| `/dashboard` | dashboard.html | 综合态势首屏：当前风险、贡献、覆盖与事件证据 |
| `/graph` | graph.html | 关系网络图谱（ECharts 力导向图） |
| `/guide` | guide.html | 使用说明：架构、数据域、算法边界 |
| `/literature` | literature.html | 文献中心：分页搜索、主题/模块筛选 |
| `/workspace` | workspace.html | 个人工作区：会话历史、追问、简报、偏好 |
| `/login` | login.html | 登录页面（共享模式） |

### 仪表盘二级页面

| 路径 | 页面 | 说明 |
|------|------|------|
| `/dashboard/indicators` | dashboard.html | 风险与指标：日风险、贡献和多源研究面板 |
| `/dashboard/events` | dashboard.html | 事件与证据：窗口内报道事件与原始出处 |
| `/dashboard/data` | dashboard.html | 数据状态：观测覆盖、采集运行和来源立场 |

### 研究子页面

| 路径 | 页面 | 说明 |
|------|------|------|
| `/spatial` | research.html | 空间统计：地缘位势、空间自相关与局部检验 |
| `/multimodal` | research.html | 多源对照：完整月份对齐、年度原值 |
| `/analysis/risk` | research.html | 风险解读：基于已保存观测解释分数构成 |
| `/research/methodology` | research.html | 指标与计算方法：从原始资料到结果 |
| `/research/history` | research.html | 历史背景：独立参考资料 |

---

## 7. 其他业务接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chain` | 分步链式推理，参数 `chain_depth` 为 1-4 |
| GET | `/api/history` | 查询历史事件 |
| GET | `/api/multimodal` | 完整月对齐、年度原值及有样本门槛的探索性相关 |
| GET | `/api/geo_potential` | 地缘位势与空间自相关 |
| GET | `/api/diagnostic` | 等长前后期真实指标贡献比较，非因果归因 |
| GET | `/api/alert` | 当前预警与阈值线 |
| POST | `/api/alert/acknowledge` | 确认预警 |
| GET/POST | `/api/scheduler` | 调度状态或手动触发任务 |
| GET | `/api/sources/health` | 数据源健康状态（成功率/降级监控），响应示例见下 |
| GET | `/api/kg/query` | 查询知识图谱 |
| POST | `/api/kg/seed` | 写入知识图谱种子数据 |
| GET | `/api/network` | 关系网络分析 |
| GET | `/api/network/actors` | 8 节点行为体关系网络（Goldstein 净值有向加权） |
| GET | `/api/report` | 导出 HTML/DOCX 或 JSON 快照；支持通用筛选与 revision |

`/api/analyze` 的 `data.warnings` 列出 NER、情感、LLM、持久化及贡献分析降级原因。它不采集GDELT、不使用年度代理、不写Neo4j或正式预警。共享模式持久化失败会使任务失败；任务异常日志只记录类型和任务ID，不回显用户输入。

### 多模态窗口与年度原值
`GET /api/multimodal?days=365&end_date=2025-12-31&region=MMR`；也可使用 `months=12`，但与days同时出现返回400。默认12个月，支持来源筛选。

- `data.aligned`：仅包含窗口内完整自然月，含原值、单位、独立观测ID、来源、质量及冲突说明。情感风险为 `1 - mean(sentiment_score)`，越大越负面。
- `data.annual.observations`：仅完整年度真实观测，含 `indicator/value/unit/period_start/period_end/source/product/dataset_version`；0保留，多版本不合并。`invalid_count` 给出隔离数量。
- `data.correlations`：每对至少12个不重复有效完整月且非零方差；否则值为null，另有 `sample_counts/reasons`。不同夜光来源/单位/版本不混算。
- `metadata` 标明请求范围、完整月数、时区和 `multimodal-v3`。年度不展开到月度；即使月度为空，页面仍显示年度表。

### 报告快照
`GET /api/report?format=json&days=30&end_date=2026-03-31&region=MMR` 获取来源明细、覆盖、版本和快照；切换 `format=html|docx` 时保留全部筛选和revision。请求失败/快照过期先刷新页面，不能下载与当前显示不一致的旧报告。

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
      "stance": {"stance": "domestic", "label": "境内中文", "note": "面向在缅华人读者的中文新闻"},
      "records": [
        {"time": "2026-08-05T11:27:10", "ok": true, "count": 6, "error": null}
      ]
    }
  }
}
```

`status` 取值（以“当前是否可用”为主）：`healthy`（最近一次成功，含降级通道成功）/ `degraded`（最近一次失败但 24 小时内有过成功）/ `dead`（最近一次失败且 24 小时无成功，或最近 3 次全失败）。
`stance` 为信源立场标注（`domestic/independent/ethnic/aggregator/international/unlabeled`），**仅展示不参与评分**，配置见 `config.yaml` 的 `source_stance`，背景见 `docs/source_landscape.md`。
只返回当前仍在采集的源；已停用源保留历史记录但不展示。文件模式使用 `DATA_ROOT/processed/source_health.json`。

### `/api/risk/explain` 风险成因解读

`POST` 请求体：`{"date": "2026-10-20", "model": "glm-4-flash"}`（两者均可省略：date 默认最新观测，model 默认 GLM-4-Flash，可选 `deepseek-chat`）。

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "date": "2026-10-20",
    "model_version": "glm-4-flash",
    "answer": {
      "summary": "2026-10-20 风险分 55.06，属于中风险，主要来自冲突频次与情感负面度。",
      "top_drivers": [{"indicator": "冲突频次", "points": 31.8164},
                      {"indicator": "情感负面度", "points": 23.2455}],
      "explanations": ["冲突频次贡献最大，占 31.8164 分。"],
      "suggestions": ["出行时注意安全，避免前往冲突频次高的地区。"],
      "data_gaps": ["夜光变化数据缺失"],
      "confidence": "高"
    },
    "evidence": {"indicator_coverage": 0.55, "source_stances": {"independent": 27, "aggregator": 21}},
    "prompt_version": "risk-explain-v1",
    "disclaimer": "辅助研判，需人工核查；指标贡献不是因果证明。"
  }
}
```

约束：模型只能引用证据中的数值，禁止因果断言与链接；校验不过重试一次，再不过返回 `status=degraded` 的规则解读（`model_version=rule-based`）。覆盖率低于 0.5 或样本少于 5 时置信度最高只能为“中”。同一 (日期, 输入指纹, 模型) 缓存 10 分钟，进程内每分钟最多 6 次调用。

---

## 8. 会话与安全接口

> 以下接口仅在共享模式（`SHARED_MODE=true`）下可用。本地免登录模式无需调用。

### `GET /api/session`

获取当前会话状态与 CSRF token。无需登录即可调用。

#### 响应示例（未登录）

```json
{
  "success": true,
  "data": {
    "shared_mode": true,
    "csrf_token": "aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789ABCD",
    "user": null
  }
}
```

#### 响应示例（已登录）

```json
{
  "success": true,
  "data": {
    "shared_mode": true,
    "csrf_token": "nEwToKeNaFtErLoGiN0123456789ABCDEFghij",
    "user": {
      "id": "u-xxxxxxxx",
      "username": "analyst1",
      "role": "analyst"
    }
  }
}
```

### `POST /api/login`

登录。请求体须含 `username` 和 `password`；须携带 Cookie 和 `X-CSRF-Token` header。限流：每 IP 10 次 / 300 秒。

#### 请求体

```json
{
  "username": "analyst1",
  "password": "secure-password-here"
}
```

#### 响应示例

```json
{
  "success": true,
  "data": {
    "csrf_token": "nEwToKeNaFtErLoGiN0123456789ABCDEFghij"
  }
}
```

#### 错误响应

```json
{"success": false, "error": "用户名或密码错误"}
```

### `POST /api/logout`

登出，清除会话。无请求体。

```json
{"success": true}
```

### `GET /api/jobs/<job_id>`

查询异步任务状态（仅共享模式）。`data` 包含 `status`（queued / running / done / failed）和结果摘要。

### `GET /api/analyses/<run_id>`

获取本人或已共享的分析记录详情。

### `POST /api/analyses/<run_id>/share`

共享或撤回共享分析记录。仅记录所有者可操作。

```json
{"shared": true}
```

---

## 9. 产品功能接口

> 以下接口由 `product_api.py` Blueprint 提供。文献相关接口需 `ENABLE_LITERATURE=true`。

### `GET /api/product/status`

返回所有功能开关状态。无需登录。

#### 响应示例

```json
{
  "success": true,
  "data": {
    "literature": false,
    "workspace": false,
    "risk_panels": false,
    "risk_briefing": false,
    "auto_briefing": false,
    "un_news": false,
    "shared_mode": false
  }
}
```

### `GET /api/view-options`

返回前端筛选器可选的区域和来源列表。无需登录。

#### 响应示例

```json
{
  "success": true,
  "data": {
    "regions": [
      {"code": "MMR", "name": "缅甸全国"},
      {"code": "MMR001", "name": "克钦邦"},
      {"code": "MMR002", "name": "掸邦"}
    ],
    "sources": ["缅华网", "GDELT", "Google News (en)", "Google News (zh)"],
    "warnings": []
  }
}
```

---

## 10. 文献中心接口

> 需 `ENABLE_LITERATURE=true`。由 `product_api.py` 提供。

### `GET /api/literature`

分页检索文献。支持筛选参数。

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| q | string | "" | 搜索关键词（题名/作者/摘要） |
| offset | int | 0 | 分页偏移 |
| limit | int | 20 | 每页条数 |
| category | string | null | 主题分类 |
| module | string | null | 对应技术模块 |
| indicator | string | null | 对应指标 |
| topic | string (可重复) | null | 研究主题标签 |
| core_only | string | "false" | 是否仅核心文献 |
| year_from | int | null | 起始年份 |
| year_to | int | null | 结束年份 |

#### 响应示例

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "KB-001",
        "title": "缅甸北部冲突的地缘环境分析",
        "authors": ["张三"],
        "year": 2024,
        "abstract": "本文分析了……",
        "category": "地缘冲突",
        "module": "风险评分",
        "indicator": "冲突频次",
        "topics": ["地缘位势与影响力测算"],
        "core": true,
        "link_type": "doi",
        "link_url": "https://doi.org/10.xxxx/example"
      }
    ],
    "total": 645,
    "offset": 0,
    "limit": 20,
    "overview": {
      "total_records": 645,
      "categories": {"地缘冲突": 120, "经济分析": 85},
      "modules": {"风险评分": 200, "态势研判": 150},
      "year_range": [2018, 2026]
    }
  }
}
```

### `GET /api/literature/<record_id>`

获取单条文献的完整公开字段。

#### 响应示例

```json
{
  "success": true,
  "data": {
    "id": "KB-001",
    "title": "缅甸北部冲突的地缘环境分析",
    "authors": ["张三"],
    "year": 2024,
    "abstract": "本文分析了……",
    "category": "地缘冲突",
    "module": "风险评分",
    "indicator": "冲突频次",
    "topics": ["地缘位势与影响力测算"],
    "core": true,
    "link_type": "doi",
    "link_url": "https://doi.org/10.xxxx/example",
    "tags": ["缅甸", "克钦", "冲突"]
  }
}
```

---

## 11. 个人工作区接口

> 需 `ENABLE_WORKSPACE=true` + 共享模式 + PostgreSQL。由 `workspace_api.py` 提供。
> 所有请求须携带登录会话Cookie；`owner_id` 由服务端从会话确定，客户端不得指定。

### `GET /api/workspace/conversations`

获取当前用户的会话列表。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| offset | int | 0 | 分页偏移 |

#### 响应示例

```json
{
  "success": true,
  "data": [
    {
      "id": "conv-xxxxxxxx",
      "title": "克钦邦冲突分析",
      "created_at": "2026-10-01T10:00:00",
      "updated_at": "2026-10-20T14:30:00",
      "message_count": 6
    }
  ]
}
```

### `POST /api/workspace/conversations`

创建新会话。

```json
{"title": "新对话"}
```

#### 响应（201）

```json
{"success": true, "data": {"id": "conv-newid"}}
```

### `GET /api/workspace/conversations/<cid>`

获取单个会话详情（含消息列表）。

### `PATCH /api/workspace/conversations/<cid>`

重命名会话。`{"title": "新标题"}`（1～160 字符）。

### `DELETE /api/workspace/conversations/<cid>`

删除会话。

### `GET /api/workspace/conversations/<cid>/export`

导出会话为 JSON 文件（Content-Disposition: attachment）。

### `POST /api/workspace/conversations/<cid>/messages`

在指定会话中发送 AI 研判请求。需 `ENABLE_RISK_BRIEFING=true`。

#### 请求体

```json
{
  "question": "请分析近期克钦邦的冲突态势",
  "days": 30,
  "region": "MMR001"
}
```

须携带 `Idempotency-Key` header（1～128 字符）防止重复提交。

#### 响应（202）

```json
{
  "success": true,
  "data": {
    "request_id": "req-xxxxxxxx",
    "status": "queued",
    "message": "已入队；需独立worker运行，未启动时不会生成回答"
  }
}
```

### `POST /api/workspace/briefings`

请求独立简报（不绑定特定会话）。参数同上。

### `GET /api/workspace/requests/<rid>`

查询研判请求的结果。

### `GET /api/workspace/preferences`

获取当前用户偏好设置。

#### 响应示例

```json
{
  "success": true,
  "data": {
    "region": "MMR",
    "language": "zh-CN",
    "theme": "light"
  }
}
```

### `PUT /api/workspace/preferences`

更新偏好。请求体为键值对。

### `PUT /api/workspace/messages/<mid>/feedback`

为消息提交反馈评分。

### `DELETE /api/workspace/messages/<mid>/feedback`

撤回反馈。

### `POST /api/workspace/usage`

记录功能使用事件。`{"feature": "risk_briefing"}`

### `POST /api/workspace/suggestion`

获取系统推荐建议。

### `GET /api/workspace/briefings/latest`

获取最新自动研判简报结果。

---

## 12. 多源风险面板接口

> 需 `ENABLE_RISK_PANELS=true`。由 `risk_api.py` 提供。
> 写操作（`POST /api/risk_panels/build`）还需共享模式 + admin 角色。

### `GET /api/risk_panels`

查询月/年度风险面板。

#### 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| frequency | string | "monthly" | 面板频率：monthly / annual |
| count | int | 12/5 | 查询期数数量 |
| end_date | string | null | 截止日期 |
| mode | string | "formal" | 模式：formal / proxy |
| region | string | "MMR" | 区域代码 |
| calibration_id | string | null | 校准基准 ID |
| panel_revision | string | null | 客户端缓存版本，不一致返回 409 |

#### 响应示例

```json
{
  "success": true,
  "data": {
    "panels": [
      {
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "score": 58.3,
        "status": "computed",
        "algorithm_version": "risk-v4-multisource",
        "calibration_id": "calib-001"
      }
    ],
    "revision": "rev-xxxxxxxx",
    "filters": {"frequency": "monthly", "count": 12, "mode": "formal", "region": "MMR"},
    "algorithm_version": "risk-v4-multisource",
    "unit": "风险分（非概率）",
    "calibrations": [
      {"id": "calib-001", "frequency": "monthly", "mode": "formal", "description": "审定基准 v1"}
    ],
    "note": "新月/年面板独立于旧日风险；正式缺项不合成，代理不作正式预警。"
  }
}
```

缺测期返回 `score: null` 和 `status: "not_computed"`，附原因说明。

### `GET /api/risk_sources/status`

查询各外部数据源的加载状态与批次信息。

#### 响应示例

```json
{
  "success": true,
  "data": {
    "acled": {
      "enabled": false,
      "status": "not_loaded",
      "reason": "需要myACLED授权；未获批时仅可选GDELT代理"
    },
    "ofac": {
      "enabled": false,
      "status": "not_loaded",
      "reason": "官方法律措施须有可靠生效日/类型；不以SDN对象数代替"
    },
    "idmc": {
      "enabled": false,
      "status": "not_loaded",
      "reason": "导入官方GIDD文件；年度全国统计不复制到邦省/月度"
    },
    "viirs": {
      "enabled": false,
      "status": "not_loaded",
      "reason": "本轮不联网下载；需真实月分区亮度，WB另作年度代理"
    }
  }
}
```

### `POST /api/risk_panels/build`

发起面板计算任务。需 admin 角色 + 共享模式 + `ENABLE_RISK_PANELS=true`。

#### 请求体

```json
{
  "frequency": "monthly",
  "count": 12,
  "end_date": "2026-10-31",
  "mode": "formal",
  "region": "MMR",
  "parameters": {
    "lambda": {"conflict_frequency": 0.25, "sentiment_avg": 0.20},
    "approved_version": "v4-multisource-r1"
  }
}
```

可携带 `Idempotency-Key` header 防重复提交。

#### 响应（202）

```json
{
  "success": true,
  "data": {
    "job_id": "job-xxxxxxxx"
  }
}
```

---

## 13. 快速测试命令
以下用于本机免登录模式；共享模式需带会话 Cookie 和 CSRF header。POST分析可能调用已配置的LLM，执行前确认数据可以发送给该服务。

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
