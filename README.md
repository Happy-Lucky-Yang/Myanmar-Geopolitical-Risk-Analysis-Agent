# 缅甸地缘风险智能分析系统

## 项目简介
本项目构建了一个轻量级、可复现的**缅甸地缘环境智能计算系统**，对标 `process.html` 五层技术路线（数据采集 → 清洗结构化 → 智能计算 → 态势分析 → 输出可视化），实现从多源数据采集到风险量化、地缘位势评估、态势研判与可视化的完整闭环。项目由华东师范大学本科生创新团队开发，作为"区域国别地缘环境智能计算研究"大创项目的技术实现。

> **数据说明**：已有缅华网、GDELT CSV/DOC、Google News、Irrawaddy/Frontier 及 World Bank 等采集适配器；实际可用性以 `/api/sources/health` 和当次任务为准，不保证不限流或长期可达。WB 电力推导夜光、经济推导难民均为代理估算，不是真实遥感/难民观测，不进入正式风险或多模态序列。读接口不自动发起采集。

## 本轮治理状态（实施中）
- 核心 Python 3.12；文件模式默认保留，PostgreSQL/PostGIS 为显式可选后端。
- 正式日风险按去重文章的发布业务日计算；手工分析、demo、legacy 不混入正式历史。近 N 天使用 Asia/Yangon 连续日历窗口，缺测为 null。
- 省级图使用真实事件发生地与 GADM 边界匹配；无样本灰显。省级事件规则烈度与全国日风险不是同一个指标。
- KDE 使用本地等面积投影和固定公里带宽；地图公共色标按天归一。多模态不再合成夜光或把年度代理复制为月度观测。
- `risk-v2` / `trend-v2` 与旧版基线并存。回测指标、模型字段完整率均不代表结论正确率。
- 已提供迁移、导入预演、任务队列、权限与 Compose 配置；真实 PostGIS、备份恢复、部署和算法留出集仍未验收，不能据此切换主库或上线。
- 四页保留时间/区域/来源筛选；综合态势首屏展示当前风险、贡献、覆盖与事件证据，研究面板折叠。报告绑定筛选与数据修订，数据变化须刷新。多模态只用完整月份，并可查看原值、单位、来源和拒绝相关的原因。
- 已完成阶段只读清单及候选重复核查，未移动或删除资料。最新回归范围与未验收项统一见 [验收说明](docs/acceptance.md)。
- 数据库操作与确认边界见 [数据库说明](docs/database_design.md)。不自动导入私密数据、安装系统服务、删除或归档活动运行资料。

## 团队分工
| 角色 | 姓名 | 主要任务 |
|------|------|----------|
| 组长/地缘理论 | 舒媛媛 | 地缘理论框架、指导沟通、进度督促、地科院资源对接、语料标注、**标注格式校对** |
| 数据采集/分析/可视化 | 杨雯瑾 | 数据采集、清洗、加权打分、趋势分析、知识图谱、可视化、**NER 验收脚本** |
| NLP/系统整合 | 高一翔 | NER、情感分析、大模型 API、系统整合、性能优化、文档 |
| 遥感数据处理 | 刘彦均 | 遥感数据获取解译、GeoJSON 边界、夜光指标、参与可视化 |
| 历史/地缘理论 | 薛雨恬 | 语料标注、指标定义与权重、历史冲突事件标注、**NER 语料标注（批次1，60条）**、结果校验 |

### 数据标注交付与验收
| 交付物 | 负责人 | 规模 | 存放位置 | 验收结果 |
|--------|--------|------|----------|----------|
| 历史事件集 | 薛雨恬 | 482 条 | `data/raw/historical_events.json` | ✅ 入库 |
| NER 金标准·批次1 | 薛雨恬 标注 / 舒媛媛 格式校对 | 60 条·705 实体 | `data/static/ner_annotations/ner_gold_batch1.json` | 已在 venv312 + 词典回退后端重跑基线：micro-F1 0.427（P 0.780 / R 0.293），`status = not_accepted`；金标准无人工 `event_group_id`，不当前独立留出验收 |

复跑验收：`python scripts/evaluate_acceptance.py --ner-gold data/static/ner_annotations/ner_gold_batch1.json`（默认 `--ner-split baseline`，不写入报告；需真实留出验收时先人工补齐 `event_group_id` 后传 `--ner-split holdout --output 全新路径`）。

## 五层技术路线对标（process.html）

| 层级 | 模块 | 实现情况 |
|------|------|----------|
| **一 数据采集** | 遥感/新闻/经济/历史文献 | ✅ 新闻(多源含健康监控) + 夜光(WB代理) + 经济(WB) + 历史事件(482条) |
| **二 清洗结构化** | 质量检查/多模态对齐/NER/知识图谱 | ✅ 清洗去重 + 多模态时空对齐 + LAC + spaCy 双语 NER + Neo4j 图谱(可选) |
| **三 智能计算** | LLM/轻量算法/地缘位势/链式推理 | ✅ LLM 封装 + NetworkX + **地缘位势(1/d²)** + **空间自相关(Moran's I)** + 链式推理 |
| **四 态势分析** | 描述/探索/诊断/预测 | ✅ 描述性 + 异常探测 + **诊断归因** + 趋势预测 |
| **五 输出可视化** | 态势图/智能报告/预警面板 | ✅ Folium 风险地图（省界分级填色 + 事件密度 KDE） + HTML/DOCX 报告 + 动态预警面板 |

## 项目结构
```
Myanmar-Geopolitical-Risk-Analysis-Agent/
├── config.yaml                   # 配置（权重/数据源/夜光/经济/调度）
├── requirements.txt              # Python 依赖
├── app.py                        # Flask 页面、业务 API 与健康检查
├── storage/                      # PostgreSQL repository、Schema、任务队列
├── migrations/                   # Alembic 业务表与事务修订号迁移
├── worker.py                     # 独立数据库任务消费者（显式调度）
├── compose.yaml                  # PostGIS / Gunicorn Web / worker（未部署验收）
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
│   ├── event_density.py          # 🆕 事件核密度估计 KDE（加权密度面 + 国界掩膜）
│   └── report_generator.py       # 自动化报告（Jinja2 HTML + python-docx）
├── data/                         # 数据采集与存储
│   ├── crawler.py                # 缅华网爬虫（中文）
│   ├── myanmar_now_crawler.py    # 英文新闻爬虫
│   ├── rss_crawler.py            # RSS 新闻源爬虫
│   ├── gdelt_client.py           # GDELT DOC 2.0 客户端（限速器/重试/备用通道）
│   ├── gdelt_files.py            # 🆕 GDELT 原始 CSV 通道（退避重试、连续成功水位线）
│   ├── event_store.py            # 🆕 GDELT 事件累积库（去重追加/真实日历查询/事务水位，历史不自动删除）
│   ├── gdelt_crawler.py          # GDELT 适配器
│   ├── nightlight_crawler.py     # 夜间灯光遥感（WB 代理指标）
│   ├── economic_crawler.py       # 宏观经济统计（WB API）
│   ├── historical_events.py      # 历史事件数据集（2020-2025，482 条）
│   ├── kg_seeder.py              # 知识图谱种子填充（34 节点 + 35 关系）
│   ├── source_health.py          # 数据源健康追踪（成功率/降级监控，持久化）
│   ├── admin_boundaries.py       # 🆕 行政边界加载（GADM 4.1，中英映射/质心/多边形）
│   ├── scheduler.py              # 单机兼容调度器（默认禁用；共享模式用 worker）
│   ├── static/gadm/              # 🆕 GADM 4.1 缅甸四级行政边界（学术许可，论文需注源）
│   └── raw/                      # 原始数据 + 缓存
├── visualization/
│   ├── map_gen.py                # Folium 地图生成（省界分级填色/事件密度，暗色主题）
│   └── chart_gen.py              # ECharts 图表数据（预测/阈值线/事件标注）
├── templates/                    # Flask 模板（4 个页面）
│   ├── chat.html                 # 对话分析（含诊断归因 + 链式推理）
│   ├── dashboard.html            # 🆕 综合态势仪表盘（含数据源健康卡片）
│   ├── map.html                  # 风险地图
│   └── trend.html                # 趋势预测（含预警指示灯）
├── static/
│   ├── css/style.css             # 暗色监控主题
│   └── js/                       # common.js / chat.js / trend.js / dashboard.js
├── utils/config.py               # 配置加载
├── tests/                        # 单元测试
├── docs/                         # 文档
│   ├── api_examples.md           # API 请求/响应示例
│   ├── upgrade_plan.md           # 🆕 系统升级计划（遥感/双边/KDE 等，含数据获取清单）
│   ├── database_design.md        # 数据库设计说明书
│   ├── algorithm_details.md      # 算法实现细节（含数学公式）
│   └── research_report_outline.md# 综合研究报告框架
├── run_crawler_only.py           # 独立爬虫脚本
└── run_full_pipeline.py          # 全流程集成脚本
```

## 环境配置

### 0. Python 版本

项目核心环境统一使用 **Python 3.12**。LAC/Paddle 为可选中文 NLP 后端，
不兼容时明确回退到 jieba，不阻止 Web 与数据库能力运行；不同后端的指标须重新评测。

### 1. 创建虚拟环境（推荐）
```bash
# Windows：使用已安装的 Python 3.12，不修改全局解释器
py -3.12 -m venv venv312
.\venv312\Scripts\python.exe -m pip install -r requirements.txt
# Linux：python3.12 -m venv venv312；之后使用 venv312/bin/python
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python -c "import nltk; nltk.download(\"vader_lexicon\")"
python scripts/check_environment.py
```

其中英文情感资源的等价 Python 调用为 `nltk.download("vader_lexicon")`。
核心依赖：Flask、flask-cors、requests、beautifulsoup4、pandas、numpy、jieba、spaCy、snownlp、nltk、folium、pyecharts、**networkx**、**wbgapi**（World Bank）、**python-docx**、**Jinja2**。LLM 和 Neo4j 均为运行时可选能力；`openai` 或 `neo4j` 驱动、密钥或服务缺失时，系统会返回可见的降级状态。

### 3. 配置文件与密钥（新成员必读）
非敏感配置（权重/爬虫源/调度间隔等）由 `config.yaml` 管理，随仓库同步。
**密钥绝不入库**：`config.yaml` 中的 `api_key` 永远保持占位符，真实密钥写入本地 `.env`（已被 .gitignore 拦截），运行时由 `utils/config.py` 自动覆盖：

```bash
# clone 后的三步上手
 copy .env.example .env      # 1. 复制模板（Linux/Mac 用 cp）
# 2. 编辑 .env，填入你自己的 LLM_API_KEY（智谱 GLM-4-Flash 免费注册）
python app.py                # 3. 启动验证
```

> 拉取代码后若发现"LLM 分析无输出/降级"，先检查自己本地是否存在 `.env`——这是新成员最常见的"假 bug"。

> **境外数据源代理**：GDELT / Google News / 外媒 RSS 需经本地代理访问。启动 Clash 后在 `config.yaml` 顶层 `proxy` 填入地址（如 `http://127.0.0.1:7890`，也可用 `.env` 的 `PROXY` 覆盖）；留空则直连，境外源会如实记录为失败而不影响国内源。

### 4. 数据存储位置（可选外置）
爬取的新闻、缓存、去重记录、风险历史等**运行时产物**默认写入项目内 `./data`。若希望避免第三方新闻内容、日志随仓库分发，可在 `.env` 中设置 `DATA_ROOT` 指向项目外的私密目录：
```
DATA_ROOT=C:\path\to\私密目录\运行数据
```
系统会自动在该根目录下创建 `raw/processed/external` 子目录（优先级：`DATA_ROOT` 环境变量 > `config.yaml` 的 `storage.data_root` > 默认 `./data`）。
> 例外：团队手工标注的 `data/raw/historical_events.json`（历史事件集）属项目成果，**固定存放项目内随 git 同步**，不受 `DATA_ROOT` 影响。

## 运行方式

### Web 服务（默认不自动采集）
```bash
python app.py
```
默认地址：http://127.0.0.1:5000

默认仅监听本机并关闭 debug，CORS 也仅允许本机页面。若需部署到局域网或公网，
请在反向代理层配置 HTTPS、身份认证和请求限流后再修改监听地址；不要直接暴露
调度触发与 LLM 分析接口。

> Web 默认不启动采集。单机旧调度器需要显式 `ENABLE_EMBEDDED_SCHEDULER=true`；共享模式禁止内嵌调度，使用 `python worker.py` 消费数据库任务。`python worker.py --schedule` 才启用每小时正式采集任务。

### 独立运行
```bash
python run_crawler_only.py            # 单次爬取
python run_crawler_only.py --schedule # 定时爬取模式
python -m data.kg_seeder              # 填充知识图谱种子数据（需 Neo4j）
python run_full_pipeline.py --demo    # 模拟数据全流程（无需网络）
python run_full_pipeline.py --skip-crawl --skip-llm  # 本地数据离线分析
```

`--demo` 会强制关闭爬虫、GDELT、World Bank、LLM 和 Neo4j 网络访问，
并在 `DATA_ROOT/processed/demo/runs/<timestamp>/` 生成摘要、地图、趋势数据和 HTML 报告。
每个阶段均返回 `ok / skipped / degraded / failed` 状态及可见警告。

## 页面说明

| 页面 | 路由 | 说明 |
|------|------|------|
| 对话分析 | `/` | 手工文本独立分析、证据与不确定性；不更新正式日风险或共享图谱，可选链式推理 |
| 综合态势 | `/dashboard` | 推荐正式观测入口：当前风险/变化、覆盖、贡献与最近事件；研究详情按需展开 |
| 风险地图 | `/map` | 🆕 单地图多图层叠加：自定义图层面板控制分级填色/圆点光晕（互斥）+ 事件密度 KDE + 省界/国界自由组合，支持透明度调节与风险×事件叠加对比 |
| 趋势预测 | `/trend` | ECharts 时序图（实线历史 + 虚线预测 + 预警阈值线 + 事件标注）+ 报告导出 |

## API 接口一览

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/analyze` | 手工文本分析；共享模式排队202，不产生正式预警 |
| POST | `/api/chain` | 链式推理（`chain_depth` 1-4） |
| GET | `/api/gdelt` | GDELT 事件数据（`?days=7`） |
| GET/POST | `/api/scheduler` | 调度器状态 / 手动触发（crawl/gdelt/analysis/nightlight/economic） |
| GET | `/api/sources/health` | 🆕 数据源健康状态（成功率/降级监控） |
| GET | `/api/map` | 省级风险地图 HTML（`?mode=choropleth\|hybrid`） |
| GET | `/api/map/unified` | 🆕 统一地图 HTML（五图层叠加，供前端图层面板控制） |
| GET | `/api/map/events` | 🆕 事件密度 KDE 地图 HTML（`?days=7`，累积库支持 7~90 天窗口） |
| GET | `/api/trend` | 趋势数据（历史/预测/阈值线/事件标注） |
| GET | `/api/geo_potential` | 🆕 地缘位势评估（距离加权 + Moran's I + 热点） |
| GET | `/api/diagnostic` | 指标贡献及等长前后期比较（非因果归因） |
| GET | `/api/network` | 关系网络分析（中心性/社区） |
| GET | `/api/multimodal` | 多模态时空对齐（夜光×冲突×情感 + 相关性） |
| GET | `/api/history` | 历史事件（`?event_type&severity_min&year`） |
| GET | `/api/alert` | 预警状态 + 历史 + 阈值线 |
| POST | `/api/alert/acknowledge` | 确认预警 |
| GET | `/api/kg/query` | 知识图谱查询（`?entity`） |
| POST | `/api/kg/seed` | 知识图谱种子填充 |
| GET | `/api/report` | 自动化报告（`?format=html\|docx&days=30`） |
| GET | `/health` | 健康检查 |
| GET | `/api/session` | 会话角色与 CSRF token |
| GET | `/api/jobs/<job_id>` | 本人任务状态与结果 |
| GET | `/api/analyses/<run_id>` | 本人或主动共享的分析 |
| POST | `/api/analyses/<run_id>/share` | 本人分析的显式共享/撤回 |
| GET | `/api/regions` | 省级真实事件聚合与空间统计 |

详细请求/响应示例见 [docs/api_examples.md](docs/api_examples.md)。

## 核心算法说明

### 风险评分模型（0-100）
5 维加权：冲突频次(0.30) + 舆情负面度(0.25) + 夜光变化(0.20) + 难民变化(0.15) + 事件严重度(0.10)。支持**动态权重归一化**（占位指标缺失时权重按比例重分配）。

### 地缘位势评估（process.html 第三层核心）
距离加权模型：某省地缘位势 = Σ(战略中心权重 / 距离²) × 风险分。预置 5 个战略中心（中缅边境瑞丽、皎漂港、内比都、泰缅边境妙瓦底、仰光）。附带 **Moran's I 空间自相关**（衡量风险地理聚集）与**热点识别**（高-高聚集）。

### 指标贡献分析（process.html 第四层）
使用已保存的实际指标贡献，比较等长前后期；缺测、版本不一致时明确降级。贡献不是因果关系，不把全国日指标投射成固定省级分数。

### 其他
- **趋势预测**：真实日历、缺测断线、末值/EWMA/线性滚动回测、历史中位数/MAD异常；至少14有效日及80%覆盖才外推，至少56有效日和4个七日回测窗才报回测/经验区间（见 [算法说明](docs/algorithm_details.md)）
- **链式推理**：事件识别 → 影响分析 → 趋势研判 → 建议生成，逐步注入前序结果
- **关系网络**：度/介数/接近中心性 + Louvain 社区检测

## 当前进度

### 已有实现（不等于验收通过）
- **7 类数据源**：缅华网 + GDELT（CSV 直连主通道 + DOC API 备用） + Google News 双语聚合（代理） + Irrawaddy/Frontier（受限时自动降级） + 夜光(WB) + 经济(WB)，均接入数据源健康监控
- 双语 NER（LAC + spaCy）+ 双语情感（SnowNLP/VADER/GDELT tone）
- 风险评分5维接口与有效权重；真实夜光/难民仍缺测，不以代理补齐
- 趋势分析（移动平均/回归/异常/7 天预测）
- **地缘位势评估**（距离加权 1/d² + Moran's I + 热点识别）
- **诊断性归因分析**（贡献度分解 + 驱动机制解析）
- 链式推理、关系网络分析、多模态时空对齐
- 动态预警（四级阈值）、自动化报告（HTML/DOCX）
- 知识图谱种子数据（34 节点 + 35 关系）+ 历史事件集（482 条）
- 4个兼容页面、业务API与健康检查；统一筛选、加载/空数据/失败状态及报告版本绑定
- 自动定时调度器、全流程集成脚本、爬虫单元测试
- 既有数据库、算法、API与验收文档持续同步；未完成项保持显式标记

> 上述为功能实现状态，不代表已通过人工验收。NER、情感、LLM 与协作模块的
> 可重复验收方法及尚需团队提供的标注数据见 [docs/acceptance.md](docs/acceptance.md)。

### ⚠️ 部分完成 / 依赖外部条件
- 大模型 API（框架完整含重试/降级，需接入可用端点）；链式推理依赖 LLM 端点
- Neo4j 知识图谱（代码 + 种子脚本完整，`config.yaml` 中 `enabled: false`，需部署 Neo4j 实例激活）
- 国际新闻源（Irrawaddy/Frontier）受反爬与网络环境限制，需代理接入后恢复；GDELT 受 IP 配额限制，限流防护已内置
- 指标贡献比较依赖等长窗口内足够真实观测及一致版本，样本不足明确留空
- 夜光/经济为 **World Bank 代理指标**（非 NASA VIIRS 原始栅格，属轻量替代方案）

### ❌ 后续工作建议（详见 [docs/upgrade_plan.md](docs/upgrade_plan.md)）
- **遥感升级包**：VIIRS 夜光原始栅格接入（待老师提供数据） + 事件核密度分析 KDE（**已实施**：事件累积库增量积累 + 多信源互证加权 + 栅格渲染，`/api/map/events`） + 缅甸省级边界 GeoJSON（**已到位 GADM 4.1 四级**；MIMU 权威版备份存于外置 DATA_ROOT 不入 git）
- **双边关系评估模块（已设计暂缓）**：GDELT 国家对合作/冲突指数 + 贸易依存 + 政策监测，六行为体关系雷达，先作独立面板不动五维权重
- **省级定位验收**：已用事件坐标/明确发生地匹配边界，去掉全国分乘固定省系数；真实地名歧义与边界许可仍需人工核验
- **知识图谱前端可视化页面**（当前为 API + Neo4j Browser，可增 ECharts 关系图页面）
- 核验已有历史事件来源及独立事件分组，补齐 NER 校准/留出集，不以条目数量代替质量
- 深度学习 NER 模型、社交媒体数据源、实时流式处理
- 持续扩充边界条件与外部服务兼容性测试

## 数据可信度标记规范
| 标记 | 含义 | 示例 |
|------|------|------|
| 官方 | API/权威数据源直接获取 | World Bank GDP |
| 估算 | 有限数据模型推断 | 夜光代理、难民估算 |
| 线性插补 | 仅显式估算视图的内部缺测插补，不新增独立样本 | 不用于正式预测/预警 |
| 合成 | 仅 demo 隔离空间，不用于正式降级 | 演示夹具 |
| 缺失/过期 | 没有有效观测或不在窗口 | null / stale，不能解释为低风险 |

## 团队协作规范
- Git 分支：`main` 稳定版、`dev` 开发分支、`feature/xxx` 功能分支
- 提交格式：`[模块] 简短描述`，如 `[geo_potential] 添加距离加权位势模型`
- 每周同步，使用 `tests/` 验证核心函数
- **密钥红线**：禁止将真实密钥写入 config.yaml 或任何被 git 追踪的文件；禁止 `git add -f .env`；密钥通过私聊传递或各自注册（推荐后者）
- **数据不同步是正常现象**：`data/raw/` 爬取数据不入库，各成员本地趋势图/风险历史不同属预期行为；演示前在演示机提前 1-2 天运行调度器积累数据，或由数据负责人打包共享（网盘，不走 git）

## 致谢
感谢胡志丁老师、吴苑彬老师提供实验室大模型资源和地缘理论指导。本项目依托华东师范大学地缘环境智能计算实验室。

## 许可证
MIT（待定）
