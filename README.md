# 缅甸地缘风险智能分析系统

## 项目简介
本项目构建了一个轻量级、可复现的**缅甸地缘环境智能计算系统**，对标 `process.html` 五层技术路线（数据采集 → 清洗结构化 → 智能计算 → 态势分析 → 输出可视化），实现从多源数据采集到风险量化、地缘位势评估、态势研判与可视化的完整闭环。项目由华东师范大学本科生创新团队开发，作为"区域国别地缘环境智能计算研究"大创项目的技术实现。

> **数据说明**：系统已接入 **6 类数据源**：缅甸缅华网（中文）、GDELT 全球事件数据库（DOC 2.0 API，中英双语）、Myanmar Now + The Irrawaddy（英文媒体）、Frontier Myanmar + DVB（RSS 订阅）、**World Bank 夜间灯光代理指标（电力覆盖率/传输损耗）**、**World Bank 宏观经济统计（GDP/通胀/贸易/难民推断）**。夜光与经济指标通过 World Bank Open Data API 免费获取，标注为"估算/官方"，遵循数据可信度标记规范。

## 团队分工
| 角色 | 姓名 | 主要任务 |
|------|------|----------|
| 组长/地缘理论 | 舒媛媛 | 地缘理论框架、指导沟通、进度督促、地科院资源对接 |
| 数据采集/分析/可视化 | 杨雯瑾 | 数据采集、清洗、加权打分、趋势分析、知识图谱、可视化 |
| NLP/系统整合 | 高一翔 | NER、情感分析、大模型 API、系统整合、性能优化、文档 |
| 遥感数据处理 | 刘彦均 | 遥感数据获取解译、GeoJSON 边界、夜光指标、参与可视化 |
| 历史/地缘理论 | 薛雨恬 | 语料标注、指标定义与权重、历史冲突事件标注、结果校验 |

## 五层技术路线对标（process.html）

| 层级 | 模块 | 实现情况 |
|------|------|----------|
| **一 数据采集** | 遥感/新闻/经济/历史文献 | ✅ 新闻(4源) + 夜光(WB代理) + 经济(WB) + 历史事件(48条) |
| **二 清洗结构化** | 质量检查/多模态对齐/NER/知识图谱 | ✅ 清洗去重 + 多模态时空对齐 + LAC NER + Neo4j 图谱(可选) |
| **三 智能计算** | LLM/轻量算法/地缘位势/链式推理 | ✅ LLM 封装 + NetworkX + **地缘位势(1/d²)** + **空间自相关(Moran's I)** + 链式推理 |
| **四 态势分析** | 描述/探索/诊断/预测 | ✅ 描述性 + 异常探测 + **诊断归因** + 趋势预测 |
| **五 输出可视化** | 态势图/智能报告/预警面板 | ✅ Folium 热力图 + HTML/DOCX 报告 + 动态预警面板 |

## 项目结构
```
Myanmar-Geopolitical-Risk-Analysis-Agent/
├── config.yaml                   # 配置（权重/数据源/夜光/经济/调度）
├── requirements.txt              # Python 依赖
├── app.py                        # Flask 主入口（4 页面 + 20 API 接口）
├── analyzer/                     # 核心分析模块
│   ├── data_loader.py            # 数据读取与清洗
│   ├── ner.py                    # 命名实体识别（LAC）
│   ├── sentiment.py              # 双语情感分析（SnowNLP + VADER + GDELT tone）
│   ├── llm_client.py             # 大模型 API 封装
│   ├── prompts.py                # 提示词模板库
│   ├── risk_scorer.py            # 加权打分模型（0-100，动态权重归一化）
│   ├── trend.py                  # 趋势（移动平均/回归/异常/预测）
│   ├── knowledge_graph.py        # Neo4j 知识图谱操作
│   ├── network_analyzer.py       # NetworkX 关系网络分析（中心性/社区）
│   ├── geo_potential.py          # 🆕 地缘位势评估（距离加权 1/d² + Moran's I）
│   ├── diagnostic.py             # 🆕 诊断性归因分析（驱动机制解析）
│   ├── chain_reasoner.py         # 链式推理（4 步：识别→影响→趋势→建议）
│   ├── multimodal_aligner.py     # 多模态时空对齐（夜光×冲突×情感）
│   ├── alert_monitor.py          # 动态预警（红/橙/黄/绿四级）
│   └── report_generator.py       # 自动化报告（Jinja2 HTML + python-docx）
├── data/                         # 数据采集与存储
│   ├── crawler.py                # 缅华网爬虫（中文）
│   ├── myanmar_now_crawler.py    # 英文新闻爬虫
│   ├── rss_crawler.py            # RSS 新闻源爬虫
│   ├── gdelt_client.py           # GDELT DOC 2.0 客户端
│   ├── gdelt_crawler.py          # GDELT 适配器
│   ├── nightlight_crawler.py     # 夜间灯光遥感（WB 代理指标）
│   ├── economic_crawler.py       # 宏观经济统计（WB API）
│   ├── historical_events.py      # 历史事件数据集（2020-2025，48 条）
│   ├── kg_seeder.py              # 知识图谱种子填充（34 节点 + 35 关系）
│   ├── scheduler.py              # 统一定时调度器（后台线程）
│   └── raw/                      # 原始数据 + 缓存
├── visualization/
│   ├── map_gen.py                # Folium 热力地图（暗色主题 + 详细弹窗）
│   └── chart_gen.py              # ECharts 图表数据（预测/阈值线/事件标注）
├── templates/                    # Flask 模板（4 个页面）
│   ├── chat.html                 # 对话分析（含诊断归因 + 链式推理）
│   ├── dashboard.html            # 🆕 综合态势仪表盘
│   ├── map.html                  # 风险地图
│   └── trend.html                # 趋势预测（含预警指示灯）
├── static/
│   ├── css/style.css             # 暗色监控主题
│   └── js/                       # common.js / chat.js / trend.js / dashboard.js
├── utils/config.py               # 配置加载
├── tests/                        # 单元测试
├── docs/                         # 文档
│   ├── api_examples.md           # API 请求/响应示例
│   ├── database_design.md        # 数据库设计说明书
│   ├── algorithm_details.md      # 算法实现细节（含数学公式）
│   └── research_report_outline.md# 综合研究报告框架
├── run_crawler_only.py           # 独立爬虫脚本
└── run_full_pipeline.py          # 全流程集成脚本
```

## 环境配置

### 1. 创建虚拟环境（推荐）
```bash
python -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # Linux/Mac
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```
核心依赖：Flask、flask-cors、requests、beautifulsoup4、pandas、numpy、LAC、snownlp、nltk、openai、folium、pyecharts、**networkx**、**wbgapi**（World Bank）、**python-docx**、**Jinja2**、neo4j（可选）。

### 3. 配置文件
程序通过 `config.yaml` 读取配置（大模型地址、爬虫源、风险权重、夜光/经济数据源、调度间隔、Neo4j）。API 密钥等敏感信息可放入 `.env`。

## 运行方式

### Web 服务（含自动定时爬取）
```bash
python app.py
```
默认地址：http://127.0.0.1:5000

> Flask 启动时自动启动后台调度线程：缅华网每 4h、GDELT 每 6h、分析每 12h、夜光每 7 天、经济每 30 天。间隔可在 `config.yaml` 的 `scheduler` 段调整。

### 独立运行
```bash
python run_crawler_only.py            # 单次爬取
python run_crawler_only.py --schedule # 定时爬取模式
python -m data.kg_seeder              # 填充知识图谱种子数据（需 Neo4j）
python run_full_pipeline.py --demo    # 模拟数据全流程（无需网络）
```

## 页面说明

| 页面 | 路由 | 说明 |
|------|------|------|
| 对话分析 | `/` | 粘贴新闻文本 → 实体/情感/风险/大模型/**诊断归因**/GDELT，可选**链式推理** |
| 综合态势 | `/dashboard` | 🆕 预警面板 + 地缘位势 + 空间自相关 + 关系网络 + 诊断归因 + 多源融合图 + 历史时间线 |
| 风险地图 | `/map` | Folium 缅甸省级风险热力图（暗色主题 + 详细弹窗 + 多源标注） |
| 趋势预测 | `/trend` | ECharts 时序图（实线历史 + 虚线预测 + 预警阈值线 + 事件标注）+ 报告导出 |

## API 接口一览（20 个）

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/analyze` | 文本分析（实体/情感/风险/LLM/诊断归因/预警） |
| POST | `/api/chain` | 链式推理（`chain_depth` 1-4） |
| GET | `/api/gdelt` | GDELT 事件数据（`?days=7`） |
| GET/POST | `/api/scheduler` | 调度器状态 / 手动触发（crawl/gdelt/analysis/nightlight/economic） |
| GET | `/api/map` | Folium 地图 HTML |
| GET | `/api/trend` | 趋势数据（历史/预测/阈值线/事件标注） |
| GET | `/api/geo_potential` | 🆕 地缘位势评估（距离加权 + Moran's I + 热点） |
| GET | `/api/diagnostic` | 🆕 诊断性归因（`?days=14` 变化归因） |
| GET | `/api/network` | 关系网络分析（中心性/社区） |
| GET | `/api/multimodal` | 多模态时空对齐（夜光×冲突×情感 + 相关性） |
| GET | `/api/history` | 历史事件（`?event_type&severity_min&year`） |
| GET | `/api/alert` | 预警状态 + 历史 + 阈值线 |
| POST | `/api/alert/acknowledge` | 确认预警 |
| GET | `/api/kg/query` | 知识图谱查询（`?entity`） |
| POST | `/api/kg/seed` | 知识图谱种子填充 |
| GET | `/api/report` | 自动化报告（`?format=html\|docx&days=30`） |
| GET | `/health` | 健康检查 |

详细请求/响应示例见 [docs/api_examples.md](docs/api_examples.md)。

## 核心算法说明

### 风险评分模型（0-100）
5 维加权：冲突频次(0.30) + 舆情负面度(0.25) + 夜光变化(0.20) + 难民变化(0.15) + 事件严重度(0.10)。支持**动态权重归一化**（占位指标缺失时权重按比例重分配）。

### 地缘位势评估（process.html 第三层核心）
距离加权模型：某省地缘位势 = Σ(战略中心权重 / 距离²) × 风险分。预置 5 个战略中心（中缅边境瑞丽、皎漂港、内比都、泰缅边境妙瓦底、仰光）。附带 **Moran's I 空间自相关**（衡量风险地理聚集）与**热点识别**（高-高聚集）。

### 诊断性归因（process.html 第四层）
分解综合风险分，量化各指标贡献占比，识别主导驱动因素，计算驱动集中度（HHI），并生成自然语言归因描述。支持时间窗口对比归因。

### 其他
- **趋势预测**：移动平均 + 线性回归外推 + Z-score 异常检测（详见 [docs/algorithm_details.md](docs/algorithm_details.md)）
- **链式推理**：事件识别 → 影响分析 → 趋势研判 → 建议生成，逐步注入前序结果
- **关系网络**：度/介数/接近中心性 + Louvain 社区检测

## 当前进度

### ✅ 已完成
- **6 类数据源**：缅华网 + GDELT + Myanmar Now/Irrawaddy + RSS + 夜光(WB) + 经济(WB)
- 双语 NER（LAC）+ 双语情感（SnowNLP/VADER/GDELT tone）
- 风险评分 5 维指标**全部接入**（动态权重归一化）
- 趋势分析（移动平均/回归/异常/7 天预测）
- **地缘位势评估**（距离加权 1/d² + Moran's I + 热点识别）
- **诊断性归因分析**（贡献度分解 + 驱动机制解析）
- 链式推理、关系网络分析、多模态时空对齐
- 动态预警（四级阈值）、自动化报告（HTML/DOCX）
- 知识图谱种子数据（34 节点 + 35 关系）+ 历史事件集（48 条）
- **4 个前端页面 + 20 个 API 接口**，暗色监控主题、XSS 防护
- 自动定时调度器、全流程集成脚本、爬虫单元测试
- 完整文档（数据库设计 / 算法细节 / 研究报告框架）

### ⚠️ 部分完成 / 依赖外部条件
- 大模型 API（框架完整含重试/降级，需接入可用端点）；链式推理依赖 LLM 端点
- Neo4j 知识图谱（代码 + 种子脚本完整，`config.yaml` 中 `enabled: false`，需部署 Neo4j 实例激活）
- 诊断变化归因（需积累 ≥4 天风险历史后展示，否则优雅降级提示）
- 夜光/经济为 **World Bank 代理指标**（非 NASA VIIRS 原始栅格，属轻量替代方案）

### ❌ 后续工作建议
- **接入 NASA VIIRS 原始遥感栅格**（rasterio 提取各省夜光均值，替代 WB 电力代理）
- **地图省级风险真实化**：将 NER 提取地名精确关联到省份（当前为边境省份简化乘数）
- **知识图谱前端可视化页面**（当前为 API + Neo4j Browser，可增 ECharts 关系图页面）
- 扩充历史事件至 100+ 条并补充智库/文献来源标注
- 深度学习 NER 模型、社交媒体数据源、实时流式处理
- 扩充单元测试覆盖（当前仅爬虫模块）

## 数据可信度标记规范
| 标记 | 含义 | 示例 |
|------|------|------|
| 官方 | API/权威数据源直接获取 | World Bank GDP |
| 估算 | 有限数据模型推断 | 夜光代理、难民估算 |
| 线性插补 | 年度数据按月插值 | 月度经济序列 |
| 合成 | 无真实数据时生成 | 降级序列 |

## 团队协作规范
- Git 分支：`main` 稳定版、`dev` 开发分支、`feature/xxx` 功能分支
- 提交格式：`[模块] 简短描述`，如 `[geo_potential] 添加距离加权位势模型`
- 每周同步，使用 `tests/` 验证核心函数

## 致谢
感谢胡志丁老师、吴苑彬老师提供实验室大模型资源和地缘理论指导。本项目依托华东师范大学地缘环境智能计算实验室。

## 许可证
MIT（待定）
