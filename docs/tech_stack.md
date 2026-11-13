# 缅甸地缘风险智能分析系统 — 技术栈说明

## 系统架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      前端展示层                              │
│  HTML5 + CSS3（晨雾主题）+ 原生 JavaScript + ECharts        │
│  Folium/Leaflet.js 交互地图                                  │
├─────────────────────────────────────────────────────────────┤
│                      应用服务层                              │
│  Flask 3.0 + Blueprint 模块化路由（50+ API 端点）            │
│  Jinja2 模板引擎                                             │
├─────────────────────────────────────────────────────────────┤
│                      智能分析层                              │
│  风险评分引擎（risk-v2/v3/v4） · KDE 事件密度               │
│  空间自相关（Moran's I）· Shapley 贡献分解                  │
│  地缘位势（距离加权 1/d²）· 链式推理                        │
│  NLP 情感分析（SnowNLP/VADER）· 命名实体识别（spaCy/jieba） │
│  LLM 辅助研判（GLM-4-Flash / DeepSeek）                     │
├─────────────────────────────────────────────────────────────┤
│                      数据层                                  │
│  SQLite（事件存储）· Neo4j（知识图谱，可选）                 │
│  PostgreSQL/PostGIS（空间数据库，可选）                       │
│  YAML 配置 + .env 环境变量                                   │
├─────────────────────────────────────────────────────────────┤
│                      数据采集层                              │
│  缅华网爬虫 · GDELT API · Google News · Irrawaddy           │
│  Kachin News · UN News · World Bank API · EOG VIIRS         │
└─────────────────────────────────────────────────────────────┘
```

## 技术选型明细

### 后端

| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.12 | 主运行环境 |
| Flask | 3.0.3 | Web 框架，Blueprint 模块化 |
| flask-cors | 4.0.1 | 跨域支持 |
| SQLAlchemy | 2.0.43 | ORM 数据库抽象层 |
| PostgreSQL + PostGIS | psycopg 3.2 / GeoAlchemy2 0.18 | 空间数据库（可选） |
| Neo4j | 5.21.0 | 图数据库驱动（可选） |
| NetworkX | 3.3 | 关系网络分析 |
| Gunicorn | 23.0.0 | 生产部署 WSGI 服务器 |

### 前端

| 组件 | 版本 | 用途 |
|------|------|------|
| HTML5 + CSS3 | — | 自研「晨雾」视觉主题 |
| JavaScript (原生) | — | 无框架依赖，轻量交互 |
| ECharts | 5.x | 统计图表（趋势、指标、面板） |
| Folium + Leaflet.js | 0.17.0 | 交互式地图渲染 |
| Jinja2 | 3.1.4 | 服务端模板引擎 |

### 数据采集与处理

| 组件 | 版本 | 用途 |
|------|------|------|
| requests + BeautifulSoup4 | 2.32 / 4.12 | 新闻爬取与解析 |
| pandas | 2.2.2 | 数据清洗与聚合 |
| numpy | 1.26.4 | 数值计算 |
| scipy | 1.14.1 | 统计与科学计算 |
| wbgapi | 1.0.12 | World Bank 数据接口 |
| rasterio | 1.4.3 | 遥感 GeoTIFF 读取 |
| matplotlib | 3.9.2 | 遥感图像渲染 |

### NLP 与 AI

| 组件 | 版本 | 用途 |
|------|------|------|
| SnowNLP | 0.12.3 | 中文情感分析 |
| NLTK (VADER) | 3.8.1 | 英文情感分析 |
| spaCy | 3.7.5 | 命名实体识别（NER） |
| jieba | 0.42.1 | 中文分词 |
| openai SDK | 1.35.0 | LLM API 调用 |
| GLM-4-Flash | — | 智谱大模型（默认） |
| DeepSeek-chat | — | DeepSeek 大模型（可选） |

### 测试与工程

| 组件 | 版本 | 用途 |
|------|------|------|
| pytest | 8.2.2 | 自动化测试（482 用例） |
| Playwright | 1.55.0 | 浏览器端到端测试 |
| pyyaml | 6.0.1 | 配置文件解析 |
| Git | — | 版本控制 |

## 数据源（8 类）

| 数据源 | 类型 | 采集频率 | 说明 |
|--------|------|----------|------|
| 缅华网 | 新闻 | 每 4 小时 | 缅甸中文新闻 |
| GDELT | 事件 | 每 6 小时 | 全球事件数据库 |
| Google News | 新闻 | 定时 | 双语新闻聚合 |
| Irrawaddy | 新闻 | 定时 | 缅甸英文独立媒体 |
| Kachin News | 新闻 | 定时 | 克钦地区新闻 |
| UN News | 新闻 | 每 4 小时 | 联合国中文新闻 |
| World Bank | 经济指标 | 按需 | 夜光代理/经济数据 |
| EOG VIIRS | 遥感 | 月度 | 夜光辐射亮度（79 期） |

## 部署架构

```
用户浏览器
    ↓ HTTP/HTTPS
Flask (Gunicorn)
    ↓
┌──────────┬──────────┬──────────┐
│ SQLite   │ Neo4j    │ PostGIS  │
│ 事件库   │ 知识图谱 │ 空间库   │
└──────────┴──────────┴──────────┘
    ↓
数据采集调度器 (APScheduler)
    ↓
8 类外部数据源
```

## 功能开关体系

系统通过 `ENABLE_*` 环境变量控制可选功能模块，默认关闭：

- `ENABLE_LITERATURE` — 文献中心
- `ENABLE_WORKSPACE` — 个人工作区
- `ENABLE_RISK_PANELS` — 多源风险面板
- `ENABLE_RISK_BRIEFING` — AI 研判简报
- `ENABLE_UN_NEWS` — UN News 数据源
- `ENABLE_VIIRS` — VIIRS 遥感模块
- `ENABLE_EVIDENCE_WEIGHTING` — 证据加权 KDE

---

*文档生成时间：2026 年 11 月*
