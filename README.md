# 缅甸地缘风险智能分析系统

## 项目简介
本项目构建了一个轻量级、可复现的**缅甸地缘环境智能计算系统**，对标 `process.html` 五层技术路线（数据采集 → 清洗结构化 → 智能计算 → 态势分析 → 输出可视化），实现从多源数据采集到风险量化、地缘位势评估、态势研判与可视化的完整闭环。项目由华东师范大学本科生创新团队开发，作为"区域国别地缘环境智能计算研究"大创项目的技术实现。

> **数据说明**：已有缅华网、GDELT CSV/DOC、Google News、Irrawaddy/Frontier 及 World Bank 等采集适配器；实际可用性以 `/api/sources/health` 和当次任务为准，不保证不限流或长期可达。WB 电力推导夜光、经济推导难民均为代理估算，不是真实遥感/难民观测，不进入正式风险或多模态序列。读接口不自动发起采集。

> **答辩速览**：项目的差异化优势、与“大模型对话框”的本质区别、现场演示脚本与预期追问应答，见 [项目独特优势与中期答辩说明](docs/project_strengths.md)。

## 本轮治理状态（实施中）
- 核心 Python 3.12；文件模式默认保留，PostgreSQL/PostGIS 为显式可选后端。
- 正式日风险按去重文章的发布业务日计算；手工分析、demo、legacy 不混入正式历史。近 N 天使用 Asia/Yangon 连续日历窗口，缺测为 null。
- 省级图使用真实事件发生地与 GADM 边界匹配；无样本灰显。省级事件规则烈度与全国日风险不是同一个指标。
- KDE 使用本地等面积投影和固定公里带宽；地图公共色标按天归一。多模态不再合成夜光或把年度代理复制为月度观测。
- 正式日风险仍使用 `risk-v2` 五维；`risk-v3` 九指标是 GDELT 实验代理路径，未替换正式趋势、诊断和报告。组长终稿要求的 ACLED 死亡／事件数据、OFAC制裁和月年度面板尚未完整接入。回测与字段完整率不代表结论正确率。
- 已有九指标代理算法、四模块与文献索引；逐项差距及本轮产品功能见 [系统升级说明](docs/upgrade_v3.md)。本轮按用户决定不改指标体系，不重算真实历史。
- 已提供迁移、导入预演、任务队列、权限与 Compose 配置；真实 PostGIS、备份恢复、部署和算法留出集仍未验收，不能据此切换主库或上线。
- 四页保留时间/区域/来源筛选；综合态势首屏展示当前风险、贡献、覆盖与事件证据，研究面板折叠。报告绑定筛选与数据修订，数据变化须刷新。多模态只用完整月份，并可查看原值、单位、来源和拒绝相关的原因。
- 已完成仓库治理：清理可再生产物与散乱重复副本、将团队权威资料归档至仓库外私密目录（私密资料不入库）。最新回归范围与未验收项统一见 [验收说明](docs/acceptance.md)。
- 数据库操作与确认边界见 [数据库说明](docs/database_design.md)。不自动导入私密数据、安装系统服务、删除或归档活动运行资料。

## 个性化与文献产品入口
- `/guide`：解释 MMR、来源、数据域、各模块作用、算法边界和架构。
- `/literature`：分页搜索题名／作者／摘要，按主题、模块、指标、年份和核心标记筛选；链接区分 DOI 页面与已审核开放全文，不提供私密 PDF 下载。
- `/workspace`：复用邀请制账户，个人会话历史、连续追问、满意度、偏好及自愿功能建议；综合态势提供 AI 解读入口。数据库集成仍待隔离 PostGIS 环境验收。
- UN News 中文适配器：真实新闻日期、同站链接约束、访问规则、限流与失败隔离；未启动真实批量抓取。

新增开关 `ENABLE_LITERATURE`、`ENABLE_WORKSPACE`、`ENABLE_RISK_BRIEFING`、`ENABLE_AUTO_BRIEFING`、`ENABLE_UN_NEWS` **默认全部关闭**。仅浏览使用说明不需要启用其他能力。共享功能由部署负责人确认迁移 `0004_workspace`、账户与独立 worker 后再开启；本轮没有改部署配置或执行迁移。详细顺序与接口见 [升级说明](docs/upgrade_v3.md#产品功能与部署交接)。

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
| NER 金标准·批次1 | 薛雨恬 标注 / 舒媛媛 格式校对 | 60 条·705 实体 | `data/static/ner_annotations/ner_gold_batch1.json` | 已在 venv312 + 词典回退后端重跑基线：micro-F1 0.494（P 0.770 / R 0.363，通用词表扩充后），`status = not_accepted`；金标准无人工 `event_group_id`、且批次1已用于词典校准，不作独立留出验收 |

复跑验收：`python scripts/evaluate_acceptance.py --ner-gold data/static/ner_annotations/ner_gold_batch1.json`（默认 `--ner-split baseline`，不写入报告；需真实留出验收时先人工补齐 `event_group_id` 后传 `--ner-split holdout --output 全新路径`）。

## 五层技术路线对标（process.html）

| 层级 | 模块 | 实现情况 |
|------|------|----------|
| **一 数据采集** | 遥感/新闻/经济/历史文献 | ✅ 新闻(多源含健康监控) + 夜光(WB代理) + 经济(WB) + 历史事件(482条) |
| **二 清洗结构化** | 质量检查/多模态对齐/NER/知识图谱 | ✅ 清洗去重 + 多模态时空对齐 + LAC + spaCy 双语 NER + Neo4j 图谱(可选) |
| **三 智能计算** | LLM/轻量算法/地缘位势/链式推理 | ✅ LLM 封装 + **地缘位势(权重3:2:1·1/d²)** + **空间自相关(Moran's I / LISA / Gi\*)** + **行为体关系网络(8节点有向加权)** + **Shapley 诊断归因** + **九指标 risk-v3** + 链式推理 |
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
│   ├── risk_scorer.py            # 加权打分模型（risk-v3 九指标，0-100，动态权重归一化）
│   ├── geo_indicators.py         # 🆕 GDELT 派生九指标层（Goldstein 净值/CAMEO 分类/国家码归一）
│   ├── trend.py                  # 趋势（移动平均/回归/异常/预测）
│   ├── knowledge_graph.py        # Neo4j 知识图谱操作
│   ├── knowledge_base.py         # 🆕 文献知识库（xlsx 题录解析/索引/主题→模块映射/检索）
│   ├── network_analyzer.py       # NetworkX 关系网络（文章实体共现 + 🆕 8节点行为体网络）
│   ├── geo_potential.py          # 地缘位势评估（potential-v3：权重3:2:1 + 1/d² + 距离敏感性）
│   ├── spatial_analysis.py       # 🆕 空间自相关（Moran's I / LISA / Getis-Ord Gi*，三套权重矩阵）
│   ├── diagnostic.py             # 诊断性归因（🆕 Shapley 精确分解 + 三恒等式断言）
│   ├── evaluation.py             # NLP 验收评测（实体 micro-F1 / 情感一致率）
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
│   ├── upgrade_plan.md           # 系统升级计划（遥感/KDE 等；双边/五维已被 v3 取代）
│   ├── upgrade_v3.md             # 🆕 risk-v3 九指标 + 四模块 + 知识库升级方案说明
│   ├── database_design.md        # 数据库设计说明书
│   ├── algorithm_details.md      # 算法实现细节（含数学公式）
│   ├── acceptance.md             # 验收与交付状态（回归范围 + 未验收项）
│   ├── project_strengths.md      # 🆕 项目独特优势与中期答辩说明（差异化叙述 + 演示脚本 + Q&A）
│   └── research_report_outline.md# 综合研究报告框架
├── run_crawler_only.py           # 独立爬虫脚本
└── run_full_pipeline.py          # 全流程集成脚本
```

## 环境配置

### 0. Python 版本

项目核心环境统一使用 **Python 3.12**。中文 NER 后端优先级为 **LAC 统计模型 > 领域 gazetteer 词典（默认终点）> jieba 词性兜底（显式 opt-in）**。
经 batch1 金标准实测：gazetteer 词典 micro-F1 0.494（P 0.770）优于通用 jieba 0.421（P 0.416，过度标注），故 LAC 不可用时默认以 gazetteer 词典为准；
jieba 兜底实测会净增假阳性，需构造 `NERExtractor(jieba_fallback=True)` 显式开启。LAC/Paddle 为可选后端，不阻止 Web 与数据库能力运行；不同后端的指标须重新评测。

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

### 5. 可选能力激活（LLM / Neo4j）——演示前必读
系统在**无密钥、无 Neo4j** 时也能完整运行（对应接口返回可见降级状态）。若要在答辩现场演示这两项能力，按下列步骤显式激活（敏感值只写 `.env`，`config.yaml` 永远保持占位符）：

**A. 大模型链式推理（LLM）**
1. 在项目根目录 `.env` 写入真实密钥：
   ```
   LLM_API_KEY=你的智谱GLM密钥        # glm-4-flash 免费注册；也可用 LLM_API_URL / LLM_MODEL_NAME 覆盖端点
   # 或 DEEPSEEK_API_KEY=你的DeepSeek密钥
   ```
2. 重启 `python app.py`，打开对话页 `/`，选择模型后触发“链式推理”；`POST /api/chain` 返回四步结论。
3. 未配置密钥时接口不触网、返回 `degraded` 状态与原因，失败不进入成功缓存——这是设计而非 bug。

**B. Neo4j 知识图谱可视化查询**
> 就绪事实（本机已验证）：`neo4j` Python 驱动 5.21.0 已装；JDK 17（Microsoft OpenJDK 17.0.12，`C:\Program Files\Microsoft\jdk-17.0.12.7-hotspot`）满足 Neo4j 5 运行要求；KG 代码路径已通过 dry-run 与“禁用即优雅降级”验证（未连接时 `/api/kg/*` 安全返回空、绝不伪造种子回填）。
1. 启动一个 Neo4j 5 服务端（三选一）：
   - **Neo4j Desktop**（推荐，图形化）：新建项目 → 添加本地 DBMS → Start；
   - **docker**（若已安装）：`docker run -p 7474:7474 -p 7687:7687 neo4j:5`；
   - **独立 zip**（无 docker 时）：从 Neo4j Deployment Center 下载 `neo4j-community-<ver>-windows.zip`，解压后设 `JAVA_HOME` 指向上面的 JDK 17，执行 `bin\neo4j-admin dbms set-initial-password <密码>`，再 `bin\neo4j console` 启动。
   > 注：本评测环境 `dist.neo4j.org` 返回 403、且无 docker，服务端二进制需在可下载网络或经 Neo4j Desktop 获取；此为环境限制，非代码缺陷。
2. 将 `config.yaml` 的 `neo4j.enabled` 改为 `true`，并填入 `uri / user / password`（敏感值建议用 `.env` 的 `NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD` 覆盖，`config.yaml` 保持占位符）。
3. 填充种子图谱：`python -m data.kg_seeder --apply`（**必须带 `--apply`**；不带时仅打印预演且**不连接**；正式写入 34 实体节点 + 36 关系边，标记 `data_domain=demo`）。
4. 查询验证：`GET /api/kg/query?entity=缅甸`，或在 Neo4j Browser（http://localhost:7474）执行 Cypher。
5. `enabled: false` 或服务端不可达时 `/api/kg/*` 返回未激活/降级状态；NetworkX 内存网络分析（`GET /api/network`，中心性/社区）仍独立可用，不依赖 Neo4j。

> LLM 与 Neo4j 均为**运行时可选增强**；核心五层流水线（采集→清洗→计算→态势→可视化）在两者关闭时依然完整闭环。

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
| GET | `/api/gdelt` | GDELT 事件数据（`?days=7`），附 `metrics.geo_risk`（risk-v3 九指标评分） |
| GET/POST | `/api/scheduler` | 调度器状态 / 手动触发（crawl/gdelt/analysis/nightlight/economic） |
| GET | `/api/sources/health` | 🆕 数据源健康状态（成功率/降级监控） |
| GET | `/api/map` | 省级风险地图 HTML（`?mode=choropleth\|hybrid`） |
| GET | `/api/map/unified` | 🆕 统一地图 HTML（五图层叠加，供前端图层面板控制） |
| GET | `/api/map/events` | 🆕 事件密度 KDE 地图 HTML（`?days=7`，累积库支持 7~90 天窗口） |
| GET | `/api/trend` | 趋势数据（历史/预测/阈值线/事件标注） |
| GET | `/api/geo_potential` | 地缘位势评估（potential-v3：权重3:2:1 + 1/d² + 距离敏感性） |
| GET | `/api/diagnostic` | 指标贡献 + Shapley 精确分解 + 等长前后期比较（非因果归因） |
| GET | `/api/network` | 关系网络分析（文章实体共现中心性/社区 + 8节点行为体网络） |
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
| GET | `/api/knowledge_base/status` | 🆕 文献知识库状态（源表/索引/篇数/facet 概览） |
| GET | `/api/knowledge_base/search` | 🆕 文献检索（q + 主题/类别/模块/指标/相关性/年份 facet 过滤） |
| GET | `/api/knowledge_base/relevant` | 🆕 按模块/指标/主题召回支撑文献 |
| GET | `/api/knowledge_base/record/<id>` | 🆕 单篇文献题录详情（含摘要与判断理由） |

详细请求/响应示例见 [docs/api_examples.md](docs/api_examples.md)。

## 核心算法说明

### 风险评分模型（risk-v3 九指标，0-100）
要素层按**地缘关系 0.6 / 地理环境 0.2 / 地缘结构 0.2**（比 3:1:1，胡志丁等 2013 三要素 + 2014 权力等级论，各层判断矩阵 CR=0）逐级归一到九项全局权重（和=1.0）：冲突频次(0.162) + 中缅关系(0.138) + 中美博弈(0.133) + 事件严重度(0.120) + 夜光变化(0.110) + 周边邻国关系(0.102) + 流离失所(0.090) + 西方关系(0.078) + 国际制裁(0.067)。其中五项双边/结构指标由 `analyzer/geo_indicators.py` 从 GDELT 派生（Goldstein 报道量加权净值 + CAMEO 分类 + 国家码归一），带 `observed/derived/missing` 质量标记。支持**动态权重归一化**（缺测指标不稀释、不补零）。旧 `risk-v2` 五维路径保留作历史对照。详见 [系统升级方案说明](docs/upgrade_v3.md)。

### 地缘位势评估（process.html 第三层核心，potential-v3）
距离加权模型：某省地缘位势 = Σ(战略中心权重 / 距离²) × 风险分。5 个战略中心按 **3:2:1 归一**（皎漂港 0.30、瑞丽 0.30、内比都 0.20、妙瓦底 0.10、仰光 0.10，和=1.0，`validate_center_weights()` 断言）。距离用球面大圆 haversine、省邦取面积加权质心，附**距离敏感性**（Spearman ρ 比较基准/陆路排序，无路网数据时如实 `unavailable`）。空间自相关升级为 **Moran's I + LISA 局部 Moran（HH/LL/HL/LH）+ Getis-Ord Gi\***，三套权重矩阵（Queen/距离阈值/距离衰减）做敏感性，N=999 固定种子(42)单侧置换检验。

### 诊断性归因（process.html 第四层，shapley-v1）
在既有指标贡献比较之上，新增 **Shapley 精确分解**（n=9 枚举 2⁹ 子集，精确无近似），带三恒等式断言（ΣC(k)=1 / Σw·ΔZ=ΔR / Σφ(k)=R）。归因文本限“贡献份额为百分之几”，模板层过滤“导致/引起/造成”等因果动词——**是贡献分解，不是因果结论**。比较等长前后期，缺测/版本不一致明确降级，不把全国日指标投射成固定省级分数。

### 其他
- **趋势预测**：真实日历、缺测断线、末值/EWMA/线性滚动回测、历史中位数/MAD异常；至少14有效日及80%覆盖才外推，至少56有效日和4个七日回测窗才报回测/经验区间（见 [算法说明](docs/algorithm_details.md)）
- **链式推理**：事件识别 → 影响分析 → 趋势研判 → 建议生成，逐步注入前序结果
- **关系网络**：文章级实体共现（度/介数/接近中心性 + Louvain 社区）；另建 **8 节点行为体网络**（MMR/CHN/USA/EUU/THA/IND/BGD/LAO，有向加权=Goldstein 报道量净值，分合作/冲突层），输出**不进入风险合成**（避免与九指标关系类重复计量）
- **文献知识库**：组长团队 645 篇文献总表 → 结构化索引（stdlib 解析 xlsx，DTD/实体防护）+ 主题→四模块/九指标映射 + 字段加权检索；源表私密不入库，索引缓存 gitignored

## 当前进度

### 已有实现（不等于验收通过）
- **7 类数据源**：缅华网 + GDELT（CSV 直连主通道 + DOC API 备用） + Google News 双语聚合（代理） + Irrawaddy/Frontier（受限时自动降级） + 夜光(WB) + 经济(WB)，均接入数据源健康监控
- 双语 NER（LAC + spaCy）+ 双语情感（SnowNLP/VADER/GDELT tone）
- 风险评分 **risk-v3 九指标**接口与有效权重（GDELT 派生五项双边/结构指标）；真实夜光/难民仍缺测，不以代理补齐
- 趋势分析（移动平均/回归/异常/7 天预测）
- **地缘位势评估**（potential-v3：权重3:2:1 + 距离加权 1/d² + 距离敏感性）
- **空间自相关**（Moran's I + LISA + Getis-Ord Gi*，三套权重矩阵敏感性）
- **诊断性归因分析**（Shapley 精确分解 + 三恒等式断言 + 贡献度分解）
- **文献知识库一期**（组长团队 645 篇文献总表→结构化索引 + 主题/模块/指标映射 + 检索，源表私密不入库）
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
- **双边关系评估模块（GDELT 部分已在 v3 落地）**：国家对合作/冲突指数已作为九指标关系类（中缅/周边/西方）+ 8 节点行为体网络实现；**贸易依存**（UN Comtrade/中国海关月度）与**政策监测**（Google News 政策查询组）仍待数据接入
- **文献知识库二期**：PDF 全文到位后接入全文层（按 `kb_file` 关联 PDF → 切块/向量化 → 证据检索）；一期仅元数据（题录+摘要+关键词）
- **省级定位验收**：已用事件坐标/明确发生地匹配边界，去掉全国分乘固定省系数；真实地名歧义与边界许可仍需人工核验
- **关系图谱页面已提供**：`/graph` 使用 ECharts 展示文章实体共现、演示种子和国家行为体网络；不等于已连接真实 Neo4j 实例。
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
感谢胡志丁老师、吴苑斌老师提供实验室大模型资源和地缘理论指导。本项目依托华东师范大学地缘环境智能计算实验室。

## 许可证
MIT（待定）
