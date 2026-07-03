# 缅甸地缘风险分析原型系统

## 项目简介
本项目旨在构建一个轻量级、可复现的缅甸地缘风险分析原型系统。系统以新闻舆情数据为主要输入，通过自然语言处理（LAC 分词/NER、SnowNLP 情感分析）、大模型 API 调用和基础统计模型（加权打分、移动平均、线性回归），实现风险评分（0-100）、趋势判断与可视化展示。项目由华东师范大学本科生创新团队开发，作为"区域国别地缘环境智能计算研究"大创项目的技术实现。

> **数据说明**：当前系统仅接入缅甸缅华网新闻数据。申报书中规划的夜间灯光遥感数据（NASA VIIRS）和宏观经济/难民统计数据尚**未接入**，相关指标在风险评分中暂以 0 值占位。

## 团队分工
| 角色 | 姓名 | 主要任务 |
|------|------|----------|
| 组长/地缘理论 | 舒媛媛 | 地缘专业理论框架设计、与指导老师沟通协调、督促项目进展、地理科学院资源对接 |
| 数据采集/分析/可视化 | 杨雯瑾 | 数据采集、数据清洗、多指标加权打分、趋势分析、Neo4j知识图谱、可视化 |
| NLP/系统整合 | 高一翔 | 命名实体识别、情感分析、大模型API调用、系统整合、性能优化、技术文档 |
| 遥感数据处理 | 刘彦均 | 缅甸遥感数据获取解译、GeoJSON边界处理，参与可视化开发 |
| 历史/地缘理论 | 薛雨恬 | 整理与标注缅甸地缘语料、提供指标定义与权重建议、历史冲突事件标注、AI结果人工校验 |

## 项目结构
```
myanmar-risk-system/
├── .env                          # 环境变量（API密钥等，不提交git）
├── config.yaml                   # 配置文件（指标权重、数据路径等）
├── requirements.txt              # Python依赖
├── app.py                        # Flask主入口（页面路由 + API接口）
├── analyzer/                     # 核心分析模块
│   ├── __init__.py
│   ├── data_loader.py            # 数据读取与清洗
│   ├── ner.py                    # 命名实体识别（LAC）
│   ├── sentiment.py              # 情感分析（SnowNLP）
│   ├── llm_client.py             # 大模型API调用封装
│   ├── prompts.py                # 提示词模板库
│   ├── risk_scorer.py            # 加权打分模型（0-100分制）
│   ├── trend.py                  # 趋势计算（移动平均 + 线性回归）
│   └── knowledge_graph.py        # Neo4j操作（可选）
├── data/                         # 数据存储（不提交原始大文件）
│   ├── crawler.py                # 新闻爬虫（定时抓取）
│   ├── preprocessor.py           # 文本预处理（备用）
│   ├── storage.py                # 数据存储（备用）
│   ├── raw/                      # 原始爬虫数据
│   ├── processed/                # 清洗后数据
│   └── external/                 # 遥感指数、统计公报
├── visualization/                # 可视化模块
│   ├── __init__.py
│   ├── map_gen.py                # folium 热力地图生成
│   └── chart_gen.py              # pyecharts 图表数据生成
├── templates/                    # Flask模板（三个页面）
│   ├── chat.html                 # 对话分析页面
│   ├── map.html                  # 风险地图页面
│   └── trend.html                # 趋势预测页面
├── static/                       # 前端静态文件
│   ├── css/
│   │   └── style.css
│   ├── js/                       # 预留（当前 JS 内联在模板中）
│   └── images/
├── utils/                        # 工具函数
│   ├── __init__.py
│   └── config.py                 # 配置加载
├── tests/                        # 单元测试
├── docs/                         # 文档
│   └── api_examples.md           # API 请求/响应示例
├── run_full_pipeline.py          # 全流程集成测试脚本
└── news_data/                    # 旧版数据存储（兼容）
```

## 环境配置

### 1. 创建虚拟环境（推荐）
```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置文件
复制 `.env.example` 为 `.env` 并填入实际的 API 密钥和数据库连接信息（仅供参考）：
```
LLM_API_URL=http://your-lab-server.com/v1/chat/completions
LLM_API_KEY=your-secret-key
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

**程序实际通过 `config.yaml` 读取配置**（包括大模型地址、爬虫源、风险权重等）。请直接修改 `config.yaml` 中的对应字段。

### 4. 初始化数据目录
```bash
mkdir -p data/raw data/processed data/external
```

## 运行方式
```bash
python app.py
```
默认启动地址：http://127.0.0.1:5000

## 页面说明

| 页面 | 路由 | 说明 |
|------|------|------|
| 对话分析 | `/` | 用户粘贴新闻文本，点击分析获取结构化结果 |
| 风险地图 | `/map` | 展示基于 Folium 的缅甸省级风险热力地图（⚠️ 当前无历史数据时，各省风险分基于"是否边境省份"的简化规则生成，非真实评估结果） |
| 趋势预测 | `/trend` | 展示 ECharts 时间序列折线图与趋势判断（基于简单线性回归外推） |

## 当前进度（截至 2026-07-03）

### ✅ 已完成
- 缅华网新闻爬虫（含真实爬取数据 15 条，`data/raw/myanmar_news_20260615.json`）
- 中文 NER（百度 LAC）+ 英文 NER（spaCy，可选）
- 情感分析（SnowNLP，风险视角转换）
- 风险评分框架：5 项指标中 3 项已接入（冲突频次、情感均值、事件严重程度），加权 0-100 分制
- 趋势分析：移动平均、线性回归、异常检测（Z-score）、STL 简化分解
- 短期预测：基于线性回归斜率外推 7 天
- Flask Web 应用：3 个页面 + 4 个 API 接口
- 全流程集成脚本（`run_full_pipeline.py`，含 `--demo` 模式）
- 爬虫模块单元测试

### ⚠️ 部分完成
- 大模型 API 调用（代码框架完整，含重试/降级/JSON 解析，但 API 端点为占位符，需接入可用端点）
- 路透社 / 伊洛瓦底报爬虫（骨架代码已有，`config.yaml` 中 `enabled: false`，待适配启用）
- Neo4j 知识图谱（代码完整，`config.yaml` 中 `enabled: false`，`neo4j` 驱动未安装）

### ❌ 待开发
- **夜间灯光遥感数据接入**（申报书承诺 NASA VIIRS 数据，当前 `nightlight_change` 指标硬编码为 0.0，占权重 20%）
- **难民统计数据接入**（当前 `refugee_change` 指标硬编码为 0.0，占权重 15%）
- 事件严重程度精细化评估（当前基于关键词二值判断）
- 地图数据真实化（当前省份风险分基于"是否边境省份"简化乘数）
- 宏观经济统计数据整合
- 更多模块的单元测试

## 全流程测试
```bash
# 使用模拟数据运行完整流水线（无需网络）
python run_full_pipeline.py --demo

# 跳过爬取，使用已有数据
python run_full_pipeline.py --skip-crawl

# 完整流程（含爬取）
python run_full_pipeline.py
```

## 单元测试
```bash
# 运行爬虫模块测试
python -m pytest tests/test_crawler.py -v
```

## API接口说明

详细请求/响应示例见 [docs/api_examples.md](docs/api_examples.md)

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/analyze` | 接收JSON `{"text": "新闻内容"}`，返回分析结果（风险分、事件、预测） |
| GET | `/api/map` | 返回folium生成的地图HTML（字符串） |
| GET | `/api/trend` | 返回趋势数据JSON，格式：`{"dates":[], "history":[], "forecast":[]}` |
| GET | `/health` | 健康检查 |

### /api/analyze 响应示例
```json
{
  "success": true,
  "data": {
    "entities": {
      "locations": ["缅甸", "掸邦"],
      "organizations": ["克钦独立军"],
      "persons": ["敏昂莱"]
    },
    "sentiment": {
      "sentiment_score": 0.25,
      "risk_score": 0.75,
      "risk_level": "high"
    },
    "llm_analysis": {
      "event_type": "军事冲突",
      "china_myanmar_impact": "边境安全风险上升",
      "risk_warning": "短期内可能影响中缅贸易",
      "summary": "掸邦北部武装冲突升级"
    },
    "risk_score": {
      "risk_score": 72.5,
      "risk_level": "高风险"
    }
  }
}
```

## 核心模块开发指南

### 1. 爬虫开发
目标网站：缅甸中文网、伊洛瓦底报、路透社缅甸版。
- 使用 `requests` + `BeautifulSoup`
- 定时调度：`schedule` 库或系统 cron
- 输出 CSV 字段：`title, pub_time, content, url`

### 2. NER + 情感分析
- NER：LAC (https://github.com/baidu/LAC) 或 HanLP
- 情感：SnowNLP（中文）或 textblob（英文）
- 输出：每条新闻的实体列表（人名、地名、组织名）和情感分数（0-1）

### 3. 大模型API调用
- 实验室已部署模型，接口兼容 OpenAI Chat Completions 格式
- 提示词模板见 `analyzer/prompts.py`
- 注意处理超时、重试、token 限制

### 4. 风险打分模型
- 指标（3/5 已接入）：冲突事件频次（近7天，关键词匹配 ✅）、情感得分均值（SnowNLP ✅）、~~夜间灯光变化率~~（⚠️ 待接入，当前占位 0.0）、~~难民数量变化~~（⚠️ 待接入，当前占位 0.0）、事件严重程度（关键词二值判断 ✅）
- 权重：由历史/地科同学提供初始值，后续可调（注意：因 2 项指标未接入，实际有效权重仅为 65%）
- 输出：每日风险分（0-100），移动平均趋势

### 5. 可视化
- 地图：folium，根据各省风险分填充颜色（⚠️ 当前数据为简化模拟，详见"当前进度"章节）
- 趋势图：后端返回数据，前端用 ECharts 渲染

## 团队协作规范
- 代码仓库：Git（建议托管在华东师大 Git 或 GitHub 私有库）
- 分支策略：`main` 为稳定版，`dev` 为开发分支，功能分支命名 `feature/xxx`
- 提交信息格式：`[模块] 简短描述`，例如 `[crawler] 添加缅甸中文网爬虫`
- 每周同步一次，使用 `tests/` 目录下的测试脚本验证核心函数

## 常见问题

**Q: 大模型API调用失败怎么办？**
A: 检查网络和API Key，增加重试机制（最多3次）。若仍失败，降级使用本地情感分析结果作为备选。

**Q: 夜间灯光数据如何获取？**
A: ⚠️ **此项尚待开发**。计划从 NASA Earthdata 下载 VIIRS 月度合成数据，用 rasterio 提取缅甸各省平均值。目前该指标在风险评分中以 0.0 占位（权重 20%）。地信同学负责，待后续接入。

**Q: 项目中期验收前必须完成什么？**
A: 参考申报书"6.1中期成果"，核心模块的独立运行脚本 + 截图/日志证明。

## 致谢
感谢胡志丁老师、吴苑彬老师提供实验室大模型资源和地缘理论指导。本项目依托华东师范大学地缘环境智能计算实验室。

## 许可证
MIT（或其他，待定）
