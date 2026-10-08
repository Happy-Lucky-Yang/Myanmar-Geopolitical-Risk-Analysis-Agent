# 验收与交付状态

## 一键复跑

```bash
python -m pytest -q
python scripts/check_environment.py
python scripts/evaluate_acceptance.py --ner-gold data/static/ner_annotations/ner_gold_batch1.json
```

pytest 只使用 `DATA_ROOT` 临时目录、不存在的 `ENV_FILE` 与默认禁网夹具；`postgis` 标记的用例要求显式提供
专用 `*_test` 测试库，缺少时按 skip 处理，不能用 SQLite 顶替。浏览器用例复用已安装的 Edge，
页面与网络请求全部由 Playwright 拦截，不下载浏览器、不出网。

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

## 已重跑的真实基线（不代表效果通过）

- NER 批次 1 共 60 条中文金标准（无 `event_group_id`）：词典回退后端下
  micro-F1 = 0.427（P = 0.780 / R = 0.293，TP 206 / FP 58 / FN 496），
  按实体类型 F1：locations 0.470、organizations 0.130、persons 0.286、events 0.757；
  报告 `evaluation_version = nlp-eval-v2`，`status = not_accepted`。
- 情感 `tests/fixtures/sentiment_gold.json` 30 条中英基线：一致率 0.700，`status = baseline_only`，
  仅证明固定夹具未回归，不声明真实改善。
- `scripts/evaluate_acceptance.py` 默认 `--ner-split baseline`，只有具备人工事件分组、留出至少 50 条不同正文且没有运行错误时才会给出 `holdout_measured`；报告包含数据、代码、包版本、后端与分区指纹，控制台省略失败正文；输出文件禁止覆盖，只允许写入全新路径。
- 爬虫验收当天将 `config.yaml` 的 `storage.format` 设为 `csv`，确认 CSV 中至少 20 条有效新闻且字段齐全；默认 JSON 仅用于日常缓存。清洗结果中标题与正文不得同时为空，日期统一为 `YYYY-MM-DD`。

## 仍待外部条件验收

- 真实 PostGIS 16 + 3.5：空间索引 / `ST_IsValid` / GiST、并发 `claim` 与租约恢复、导入中断续跑、
  每日备份与 14 份保留策略、隔离库恢复、角色与共享读写。测试实例由运维提供，SQLite 不可替代。
- 中英别名归一与留出集 NER 效果比较：需要至少 5 个人工事件组和未参与词典调参的独立留出。
- 采集“无新文章 ≠ 失败”的显式健康语义、`source_runs` 与真实调度联跑。
- 10 并发查询压测：需真实部署环境记录耗时、失败率与硬件条件。
- Compose 构建、Gunicorn Web、独立 worker、Linux 部署与共享权限上线。
- 文件归档：清单和候选重复只读核查已完成，未移动或删除；正式归档需按类别再次确认。
- 私密目录盘点：仓库只保存必要夹具与派生产物，详细清单留在授权目录，不复制到仓库。

## 变更与回退

- 现有工作树包含前几轮修复与用户改动，全部保留；不 `reset`、`stash`、覆盖回退或自动提交。
- 数据库默认 `storage.backend = file`；`postgres` 模式失败时抛错，不写文件。
- 未确认前不导入真实私密数据、不切主库、不安装系统软件、不启动真实调度器、不删除或移动业务资料。
- 归档脚本、Shapefile 整套、活动运行目录与增量水位在归档核验前不得凭文件名判定为无用。
