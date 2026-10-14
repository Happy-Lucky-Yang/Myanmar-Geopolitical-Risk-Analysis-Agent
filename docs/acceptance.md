# 验收与交付状态

## 一键复跑

```bash
python -m pytest -q
python scripts/check_environment.py
python scripts/evaluate_acceptance.py --ner-gold data/static/ner_annotations/ner_gold_batch1.json
```

pytest 只使用 `DATA_ROOT` 临时目录、不存在的 `ENV_FILE` 与默认禁网夹具；`postgis` 标记的用例要求显式提供
专用 `*_test` 测试库，缺少时按 skip 处理，不能用 SQLite 顶替。浏览器用例复用已安装的 Edge，
页面与网络请求全部由 Playwright 拦截，不下载浏览器、不出网。`pytest.ini` 将采集范围限定为 `tests/`
（排除随项目分发的 `py312` 运行时与 `venv312`），使 `python -m pytest -q` 稳定收集 352 个用例
（本轮实测 343 通过 + 9 跳过），不再误采 `idlelib` / `site-packages` 第三方测试而报 collection error。

## 已完成并有自动化保护

- 时间与数据契约：Asia/Yangon 连续日历窗口、真实 0 与缺测 null 分离、`live / existing / manual / demo /
  legacy` 数据域、缺日不补零、覆盖率与算法版本随查询返回。
- 缺陷修复：地图瓦降级顺序、图例区分“低风险 / 无数据 / 估算”、请求序号只让最新响应生效、
  年度观测在缺失月度时仍可展示原值与版本；LLM 主链与链式共用配置校验、超时、降级；缓存键包含
  提供商、模型、提示词与 schema 版本，失败不缓存；`app.py` 与 `pipeline.py` 分离正式日指标、
  用户分析和 demo。
- 存储：SQLAlchemy 2 + psycopg 3 + Alembic；文件后端保持本地默认；PostgreSQL 失败绝不悄悄回写 JSON；
  `import_data.py` 默认 dry-run，`apply` 需要 `IMPORT_DATABASE_URL` 与精确库名确认，逐批次事务与
  `import_items` 幂等；`read_frozen` 检测文件读取期间变动，风险记录保留原 `analysis_runs` 与首次
  `file_mtime`；`verify_directory` 比对 payload 与 typed 字段，0 与 0.0 不再伪冲突，旧观测类型化字段
  仍为 legacy 时拒绝冒充升级。
- 运维：`scripts/database_ops.py` 四个动作默认预演，apply 需要 `DATABASE_TOOL_URL` 与 `--confirm-database`；
  产物只能写入授权根目录下全新单层目录，拒绝链接与越界；`pg_dump` 与摘要共享 `pg_export_snapshot`；
  备份清单校验 SHA-256；恢复仅面向以 `_test` 结尾且 public 无非扩展业务对象的隔离库；主进程继承到
  `PGHOSTADDR / PGSERVICE / PGOPTIONS` 时直接拒绝；子进程错误仅上报异常类型，不泄露连接串。
- 多人任务：数据库 `JobQueue` 行锁领取、`clock_timestamp` 心跳、租约令牌隔离；`write_transaction`
  开始与提交前双重检查；worker 失租时既不 `queue.finish` 也不写业务；重试命中同一 `job_id`
  直接返回首次 `analysis_runs` 快照，避免 `jobs.result` 与 `analysis_runs` 漂移；采集器 postgres
  模式不推进文件去重清单、不覆盖共享缓存、`source_runs` 健康通过数据库共享；日志与告警只写异常
  类型，不写入用户私密文本或供应商回显。
- 前端四页保留兼容路由，新增统一时间/区域/来源筛选；综合态势首屏显示真实事件、样本量、来源与
  定位方法；对话页分为“结论摘要—证据—不确定性—技术详情”。
- risk-v3 九指标与智能计算四模块（本轮升级，均带可复现断言）：`geo_indicators` 派生指标带
  `observed/derived/missing` 质量标记、缺测不补零（`tests/test_geo_indicators.py`）；地缘位势战略中心权重
  3:2:1 归一、权重和为 1（`tests/test_geo_potential.py`）；空间权重矩阵行标准化、全局 Moran's I 逆序不变、
  LISA 四分类、Getis-Ord Gi* 冷热点（`tests/test_spatial_multimodal.py`）；Shapley 三恒等式（ΣC=1 / ΣwΔZ=ΔR /
  Σφ=R）、归因文本禁因果动词、risk-v3 端到端（`tests/test_diagnostic_contracts.py`）；行为体网络 8 节点/
  边权区间/无自环/模块度区间（`tests/test_actor_network.py`）。
- 文献知识库一期（`tests/test_knowledge_base.py`，18 用例）：stdlib 解析 xlsx 带 DTD/实体防护（拒绝
  `<!DOCTYPE`/`<!ENTITY`）、19 列规范化、脏主题清洗、主题→四模块/九指标映射、字段加权检索与 facet 过滤、
  源表缺失优雅降级、4 个 `/api/knowledge_base/*` 端点。源表属团队私密资料**不入库**，派生索引缓存落
  gitignored 的 `DATA_ROOT/knowledge_base`；路径经 `.env` 的 `KB_SOURCE_XLSX` 提供，不写死进开源 `config.yaml`。

## 已重跑的真实基线（不代表效果通过）

- NER 批次 1 共 60 条中文金标准（无 `event_group_id`）：词典回退后端下
  micro-F1 = 0.494（P = 0.770 / R = 0.363，TP 255 / FP 76 / FN 447），
  按实体类型 F1：locations 0.571、organizations 0.242、persons 0.286、events 0.757；
  报告 `evaluation_version = nlp-eval-v2`，`status = not_accepted`。
- 本轮词典按“通用领域地名表（邻国 / 主要城市 / 常见机构 / 历史领导人）”扩充：仅纳入任何缅甸地缘
  系统都应具备的通用地理常识，未抄入金标准的复合标签（带斜杠/括号的机构全称）或文章专属人名，
  persons 精确率保持 1.000（无人名过拟合）。相较扩充前基线 micro-F1 0.427（P 0.780 / R 0.293）,
  召回率提升 0.070、F1 提升 0.067。扩充后批次 1 已属“被词典参考过的校准基线”，只能用于回归对比,
  不再代表独立泛化效果；正式 ≥70% 验收仍需未参与调参、含 ≥5 个人工事件组且 ≥50 条不同正文的
  batch2 独立留出集。召回上限取决于统计 NER 后端。本轮经用户批准已实证比较三种中文后端：
  ① LAC/paddle 在 py3.12+Windows 不可行（LAC 仅 2.1.2、需 paddle2 的 `fluid`；paddle 2.6.2 无 cp312 Win 轮子致 `libpaddle.pyd` 缺失；paddle 3.0 有 cp312 轮子但删除 `fluid`）；
  ② 通用 jieba 词性标注 micro-F1 0.421（P 0.416，过度标注），仅作词典零命中兜底亦净增 34 FP / 0 TP（整体降到 0.478）；
  ③ 领域 gazetteer 词典 0.494（P 0.770）最优。据此定 gazetteer 为默认中文回退终点，jieba 降级为 `NERExtractor(jieba_fallback=True)` 显式 opt-in。
  正式 ≥70% 召回仍需兼容环境（py3.10/Linux）恢复 LAC 或接入云端中文 NER。
- 情感 `tests/fixtures/sentiment_gold.json` 30 条中英基线：一致率 0.700，`status = baseline_only`，
  仅证明固定夹具未回归，不声明真实改善。
- `scripts/evaluate_acceptance.py` 默认 `--ner-split baseline`，只有具备人工事件分组、留出至少 50 条不同正文且没有运行错误时才会给出 `holdout_measured`；报告包含数据、代码、包版本、后端与分区指纹，控制台省略失败正文；输出文件禁止覆盖，只允许写入全新路径。
- 爬虫验收当天将 `config.yaml` 的 `storage.format` 设为 `csv`，确认 CSV 中至少 20 条有效新闻且字段齐全；默认 JSON 仅用于日常缓存。清洗结果中标题与正文不得同时为空，日期统一为 `YYYY-MM-DD`。

## 仍待外部条件验收

- 真实 PostGIS 16 + 3.5：空间索引 / `ST_IsValid` / GiST、并发 `claim` 与租约恢复、导入中断续跑、
  每日备份与 14 份保留策略、隔离库恢复、角色与共享读写。测试实例由运维提供，SQLite 不可替代。
- 中英别名归一与留出集 NER 效果比较：需要至少 5 个人工事件组和未参与词典调参的独立留出。
- Neo4j 知识图谱在线查询：`neo4j` 驱动 5.21 与 JDK 17（Microsoft OpenJDK 17.0.12）已就绪，种子 dry-run 与“禁用即优雅降级”已验证（34 实体 / 36 关系，未连接时 `/api/kg/*` 安全返回空、不伪造回填）；但本评测环境 `dist.neo4j.org` 返回 403 且无 docker，服务端二进制无法获取，真实图查询需在可下载网络或 Neo4j Desktop 下按 README 第 5.B 节激活后验证（此为环境限制，非代码缺陷）。
- 采集“无新文章 ≠ 失败”的显式健康语义、`source_runs` 与真实调度联跑。
- 10 并发查询压测：需真实部署环境记录耗时、失败率与硬件条件。
- Compose 构建、Gunicorn Web、独立 worker、Linux 部署与共享权限上线。
- 文件归档：可再生产物与散乱重复副本已清理、团队权威资料已移入仓库外私密目录（私密资料不入库）；其余过期文件归档需按类别再次确认后移动。
- 私密目录盘点：仓库只保存必要夹具与派生产物，详细清单留在授权目录，不复制到仓库。

## 变更与回退

- 现有工作树包含前几轮修复与用户改动，全部保留；不 `reset`、`stash`、覆盖回退或自动提交。
- 数据库默认 `storage.backend = file`；`postgres` 模式失败时抛错，不写文件。
- 未确认前不导入真实私密数据、不切主库、不安装系统软件、不启动真实调度器、不删除或移动业务资料。
- 归档脚本、Shapefile 整套、活动运行目录与增量水位在归档核验前不得凭文件名判定为无用。
