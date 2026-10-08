# 数据库设计说明书

## 1. 概述

本地默认保留 `file` 后端；课题组共享模式使用 PostgreSQL 16 + PostGIS 3.5、SQLAlchemy 2、psycopg 3 和 Alembic。Neo4j 仅为可选图谱能力，不作为主业务库。Python 核心运行环境为 3.12。

当前状态：已实现版本化 Schema、repository、观测导入、原值只读核验、业务导出、备份/隔离恢复工具、独立 worker、共享权限和实库测试入口；尚未完成真实 PostGIS、并发租约、备份恢复及 Compose 部署验收。离线 DDL 通过或实库测试跳过均不等于数据库验收通过。尚未导入真实私密数据或切换主存储。

### 主业务表与约束
| 数据组 | 表与约束 |
|---|---|
| 来源/文章 | `sources`、`articles`、`article_sources`；正文哈希唯一，来源外部 ID 唯一；来源与 URL 显式配对 |
| 事件/空间 | `events`、`event_evidence`、`regions`；事件外部 ID 唯一，边界代码+版本唯一 |
| 实体/关系 | `entity_mentions`、`relation_evidence`；保存文章级实体提及及共现证据、来源和算法版本；不把共现解释为现实关系 |
| 分析/指标 | `analysis_runs`、`indicator_observations`、`daily_risk`；日期+区域+算法版本+数据域唯一 |
| 采集/任务 | `source_runs`、`jobs`、`ingestion_checkpoints`；任务幂等键、租约令牌与连续成功水位 |
| 用户/审计 | `users`、`alerts`、`audit_logs`；角色约束、确认人和确认时间 |
| 导入追踪 | `import_batches`、`import_items`；文件指纹+相对路径、批次+原行号唯一 |

首版业务结构以冻结的 `storage/schema_v1.py` 和 `migrations/versions/0001_postgis.py` 为准（19 张业务表）。`0002_data_revision` 增加事务修订辅助表 `data_revision` 和业务表写入触发器；`0003_observation_versions` 增加观测结束日、产品、数据集版本和运行域，唯一键扩为来源+指标+区域+周期起点+频率+产品+版本。当前运行时与 Alembic 元数据使用 `storage/schema.py`，另有 Alembic 版本表；须迁移到 head，仅0001/0002不足以运行当前观测查询。事件为 Point，区域为 MultiPolygon；EPSG:4326、GiST 索引。常用日期及任务领取使用 B-tree 索引。JSONB 保存扩展和算法明细，不替代关键关系字段。

- 时间戳 UTC，业务日 `Asia/Yangon`；纯日期不伪造精确时刻。
- 近 N 天为截至所选日、包含当天的连续日历窗口；缺测为 null，零值有效。
- `live/existing` 正式日指标按去重文章发布日计算；`manual/demo/legacy` 不混入正式指标。手工输入默认仅本人可见，主动共享后组员可读。
- `STORAGE_BACKEND=file|postgres` 优先于 `config.yaml`；Postgres 出错不得回写 JSON。
- 文件模式仅适合单进程本地运行。共享模式必须数据库后端，不依赖进程内文件锁。
- worker 通过 `SKIP LOCKED` 领取任务，以数据库时钟判断租约。repository 写事务开始和提交前检查状态、令牌及租约，并持任务行锁；失效则回滚当前事务。此前合法提交的事务不追溯撤销，任务重试仍依赖幂等键。
- Postgres 模式四类新闻采集器直接存库，不推进文件 URL 清单，不在数据库失败后写文件；来源健康写入 `source_runs`，每次查询取每源最近20条，全历史保留。
- 队列任务不发布 Neo4j、更改共享 LLM/代理文件缓存或覆盖共享地图/报告产物。地图与报告按已提交数据快照按需生成；任务结果只在有效租约确认后发布。
- 数据修订号用于地图缓存及报告快照。带 `revision` 的 API 在读取前后校验，变化返回409；触发器更新随事务回滚，真实数据库权限及并发行为仍需实库验证。
- 原始大文件、栅格和导出留受保护目录；私密资料不复制进仓库。

### 隔离部署与迁移步骤（执行前分别确认）
以下为指导命令，未在当前环境实际执行。管理员密码、应用密码、迁移密码和会话密钥必须独立管理，勿粘贴到日志或提交到仓库。

1. 在已获批准的测试环境准备 Docker/PostGIS；使用 `.env.example` 配置，不自动安装系统软件。应用 URL 使用 `mmr_app`，迁移 URL 使用 `mmr_migrator`。Compose 中主机为 `db`，密码特殊字符须 URL 编码。
2. 先启动数据库：`docker compose up -d db`。新卷初始化脚本 `deploy/init-db.sh` 安装 PostGIS、创建应用/迁移角色、限制 public 建表权限。已有卷不会重跑角色初始化，需管理员单独核验。
3. 执行迁移：`docker compose --profile tools run --rm migrate`。迁移角色建表，应用角色仅取得业务读写及空间参考表查询权限。数据库健康不代表迁移完成；不要直接同时启动全部服务。
4. 首个管理员先预演：`docker compose --profile tools run --rm migrate python scripts/manage_users.py lab-admin --role admin`。确认目标后加 `--apply --confirm-database mmr --set-password`，交互输入密码（至少12字符，不回显）。工具保护最后一个有效管理员。勿传明文密码命令行参数。
5. 经确认后启动 Web/worker：`docker compose up -d web worker`。Web 使用 Gunicorn，监听宿主 `127.0.0.1:5000`；数据库无宿主端口映射。worker 默认仅消费队列，`--schedule` 才启用周期采集，不在 Web worker 内调度。
6. 共享访问须先部署 HTTPS 与访问控制。受控 HTTP 本地演练可显式 `COOKIE_SECURE=false`，上线恢复 true。不得把单机免登录模式公开暴露。

本机已有 PostgreSQL 时，由管理员预先建隔离库、安装 PostGIS并分配角色，再通过进程环境设置 `MIGRATION_DATABASE_URL` 后执行 `python -m alembic upgrade head`。CLI 迁移/导入/账号工具读取进程环境，不假设自动加载 `.env`。

### 导入、校验与回退边界
- 默认预演：`python scripts/import_data.py --root "授权数据目录"`，不连接业务库。仅支持白名单格式，跳过密钥、笔记、虚拟环境、manual/demo、链接目录。
- 详细报告可用 `--output "授权数据目录/全新报告.json"`；禁止覆盖已有文件、写到授权目录外或使用业务文件名。控制台仅输出计数。发现坏行退出码为2，不代表已偷偷导入。
- 仅在确认真实数据备份、目标库和权限后设置 `IMPORT_DATABASE_URL`，加 `--apply --confirm-database 精确库名` 才写入。
- 同一文件字节快照用于解析和 SHA-256，读取中变动则中止；批次与原行号可追溯，`ok` 行重跑跳过，坏行记为 `invalid`，业务错误可重试。新批次保存首次文件修改时间，避免无时间戳旧评分仅因文件被 touch 而改变导入标识。旧批次若无该字段，应保留原始文件修改时间。旧评分保留 legacy 原值，旧运行ID保存在 `original_run_id`。
- `--verify --confirm-database 精确库名` 使用 `IMPORT_DATABASE_URL` 的只读可重复读事务，对照文件字节、原行号、导入状态及原值；风险核对 `analysis_runs`，不拿最新日快照代替历史。文章允许去重后增加来源证据及保留更早日期；观测同时核对类型化字段。缺失、无效或不一致退出2。它不重试写入，也不替代空间和权限验收。
- `--apply` 与 `--verify` 互斥。重复导入跳过已成功行不是原值校验，迁移或故障恢复后仍必须运行 verify。
- 验收须核对来源数、唯一键、日期范围、样本回查、关联完整性和空间定位，并列比较文件与数据库，不能只凭导入计数切主库。
- 原文件保留；主库切换后新增写入须先导出核对。简单改回 file 不代表无损回退。Alembic 降级会移除业务表，不能作为数据回退方案。

### 只读文件治理清单
- `python scripts/inventory_files.py --root "授权目录" --scope private --hash-duplicates --output "授权目录/inventory-批次.json"` 只记录元数据、候选重复哈希和GIS套件，不移动或删除。
- 仓库模式用 `--scope repository`，静态名称引用只是线索；跳过虚拟环境、链接目录、敏感正文。私密模式不扫描笔记正文。
- 阶段清单：仓库185个文件、15,887,367字节；私密目录493个文件、136,712,784字节。清单生成后代码继续变化，这不是最终工作树哈希基线。
- 私密目录发现4组重复：2组为不同Shapefile套件的CPG/PRJ，2组为不同活动运行目录的趋势产物，均保留原位。7套Shapefile必需组件齐全，另有1个FileGDB套件。许可、实际CRS及动态引用仍需人工核验。
- 清单留在各自授权根目录，私密清单未复制入仓库。归档0、删除0；未认定可安全移动的文件，因此没有归档恢复操作。

### 数据库运维工具（默认预演）
`scripts/database_ops.py` 的四个动作均默认不连接数据库、不创建目录。`--root` 必须是已授权的受保护目录；`--name` 是该目录下单层子目录名，禁止越界、链接和覆盖。恢复指定已存在的备份目录，其他动作必须使用全新名称。

```bash
python scripts/database_ops.py verify --root "受保护产物目录" --name verify-batch
python scripts/database_ops.py export --root "受保护产物目录" --name export-batch
python scripts/database_ops.py backup --root "受保护产物目录" --name backup-batch
python scripts/database_ops.py restore --root "受保护产物目录" --name backup-batch
```

实际执行须另行确认后，在进程环境设置 `DATABASE_TOOL_URL`，加 `--apply --confirm-database 精确库名`。URL 必须包含 PostgreSQL 主机、用户和简单库名（字母/下划线开头，后续字母、数字、下划线或连字符），仅支持单个 `sslmode` 查询参数；不接受多主机、service 或 options 覆盖。密码可由受保护 passfile 提供，或只放进程环境，不放命令行/仓库。可用 `--pg-bin "已安装客户端目录"` 指定 pg_dump/pg_restore；工具不安装客户端。

- `verify`：public 下各业务表计数、日期范围、几何 SRID/有效性和迁移版本摘要；不输出行正文。
- `export`：正式文章、正式日风险、legacy 风险、正式分析、事件及水位、正式观测与逐文件 SHA-256。观测原值与类型字段不一致会失败，不制造一个“修复后”值。账号、私密输入、任务/审计、legacy事件/观测、边界及部分关系表明确不在此导出内，不能作为完整备份或无损切回文件的依据。
- `backup`：pg_dump custom 格式，仅 public；计数与 dump 共用导出的只读数据库快照。成功后才写完成清单及 SHA-256；失败保留部分产物且无完成标志。它不包含角色、授权、其他 schema 或外部原始文件，这些仍需独立备份。
- `restore`：仅名称以 `_test` 结尾、public 没有非扩展业务对象的新库，要求预装 PostGIS。校验清单和 dump SHA-256 后，pg_restore 单事务执行；核对行数、日期范围、几何和迁移版本，输出隔离恢复报告。失败库/产物保留，不删除、不切主库。
- 只恢复由本团队受控备份流程产生的可信 dump；SHA-256 是完整性检查，不是来源认证。恢复库不得启动 worker，以免重放采集任务。需重新授权并验证空间查询、用户隔离和样本原值，才算恢复验收完成。
- Linux 新建产物目录限制为0700；Windows 必须另外核查 ACL。备份可能含用户密码哈希、私密输入和任务，禁止放公共共享盘或提交仓库。

### 备份与真实集成验收
- 经确认的安全位置每日运行 backup，目标保留14份；当前工具不注册系统调度、不自动轮换删除。Windows 任务计划或 Linux timer 的安装、运行账号、备份位置和过期清理分别确认。在恢复验证成功前不清理旧备份。
- 自动实库入口：设置专用 `POSTGIS_TEST_URL` 与完全匹配的 `POSTGIS_TEST_CONFIRM`，库名必须以 `_test` 结尾；执行 `python -m pytest tests/test_postgis_integration.py -m postgis -q`。
- 测试每次新建 `test_<UUID>` schema，并保留作诊断，不自动清理；要求预装 PostGIS、账号具有创建 schema 权限。测试 URL 未提供时明确跳过。SQLite 不可替代空间、事务、并发领取验收。
- 备份恢复、角色权限、worker 失去租约后的业务写入防护、10并发压测均仍须实测，不作已完成声明。

---

## 2. JSONL 风险评分记录

**文件后端**：`DATA_ROOT/processed/daily_risk.jsonl` 为正式快照；`processed/risk_scores.jsonl` 为 legacy；`processed/manual` 和 `processed/demo` 独立。数据库后端对应 `analysis_runs` 与 `daily_risk`。

每条记录格式：
```json
{
  "date": "2026-01-15",
  "risk_score": 65.3,
  "risk_level": "中风险",
  "run_kind": "existing",
  "algorithm_version": "risk-v2",
  "region": "MMR",
  "sources": ["示例来源"],
  "sample_count": 10,
  "recorded_at": "2026-01-15T10:30:00+00:00",
  "details": {
    "indicator_coverage": 0.55,
    "data_status": "partial",
    "indicator_scores": {
      "conflict_frequency": {"value": 0.8, "weight": 0.5454545455, "contribution": 0.4363636364, "quality": "derived"},
      "sentiment_avg": {"value": 0.4766, "weight": 0.4545454545, "contribution": 0.2166363636, "quality": "derived"},
      "nightlight_change": {"value": null, "weight": 0, "contribution": null, "quality": "missing"}
    }
  }
}
```

**字段说明**：
| 字段 | 类型 | 说明 |
|------|------|------|
| date | string | 日期 (YYYY-MM-DD) |
| risk_score | float | 综合风险分 (0-100) |
| risk_level | string | 风险等级: 高/中/低 |
| details | object | 原始指标、实际有效权重、质量、贡献与覆盖率；不以当前权重反推旧结果 |

---

## 3. GDELT 文章与事件

文章文件为 `DATA_ROOT/raw/gdelt_news_*.json|csv`，下方为报道结构示意。事件累积库为 `DATA_ROOT/processed/gdelt_event_store.json`，不能把文章条数视为独立事件数。数据库对应 `articles`、`events`、`ingestion_checkpoints`。事件和连续成功批次水位同事务提交；失败批次之后不推进，成功空批次允许推进。

```json
{
  "title": "Myanmar conflict escalates...",
  "url": "https://...",
  "date": "2026-01-15",
  "source": "GDELT",
  "tone": -3.5,
  "themes": ["CONFLICT", "MILITARY"],
  "locations": [{"name": "Shan State", "lat": 21.5, "lon": 98.0}],
  "language": "en"
}
```

---

## 4. 夜光代理缓存与真实观测接口

**文件**：`DATA_ROOT/raw/nightlight_cache.json` 是 World Bank 年度电力指标推导的代理缓存，不是真实 VIIRS 遥感。下方为历史缓存示例，不进入正式多模态月序列。真实观测读 `DATA_ROOT/external/indicator_observations.jsonl` 或数据库 `indicator_observations`，此文件名已接入导入白名单。

- 专用导入要求 `source/indicator/region/unit/dataset_version`、显式 `run_kind` 与 `quality`、有限非布尔值（missing可为null）、完整日/月/年度周期。`period_end` 排他；ID 缺省由来源、周期、产品和版本生成，不包含数值。
- 同ID/同源周期产品版本改变数值会报冲突，不覆盖已有值；不同版本可并存。月度多来源/版本标记 ambiguous，不选择最后一条。年度真实值单独列出完整年度原值，不复制到月份，不参与月度相关。
- 0003 不推断旧观测质量：原 payload 保留，新字段默认为 `run_kind=legacy`、`dataset_version=legacy`、空产品、结束日null。因此旧行不会自动进入正式视图。复核来源、周期、许可和单位后，用新ID及明确复核版本导入，保留旧行；旧ID直接重导不算字段升级。

```json
{
  "nightlight_change": 0.45,
  "source": "worldbank",
  "indicators_used": ["electricity_access", "transmission_loss"],
  "raw_values": {"electricity_access": 68.5, "transmission_loss": 15.2},
  "data_year": 2024,
  "monthly_series": [],
  "fetched_at": "2026-01-15T10:30:00",
  "data_quality": "估算"
}
```

---

## 5. 经济统计数据存储

**文件**: `DATA_ROOT/raw/economic_indicators.json`。GDP 等原始值可标记官方来源，但 `refugee_change` 等代理推导值仍为估算，不能继承“官方”质量。年度数据不复制成独立月度点。

```json
{
  "gdp_growth": 2.1,
  "gdp_growth_norm": 0.484,
  "gdp_per_capita": 1180.5,
  "inflation": 18.3,
  "inflation_norm": 0.334,
  "trade_pct_gdp": 55.2,
  "trade_change_norm": 0.552,
  "refugee_change": 0.75,
  "source": "worldbank",
  "data_year": 2024,
  "fetched_at": "2026-01-15T10:30:00",
  "data_quality": "官方"
}
```

---

## 6. 历史事件数据集

**文件**: `data/raw/historical_events.json`

```json
[
  {
    "date": "2021-02-01",
    "event_type": "政变",
    "actors": ["缅甸国防军", "敏昂莱"],
    "location": "内比都",
    "severity": 5,
    "description": "缅甸军方发动政变",
    "source": "全球新闻"
  }
]
```

---

## 7. 预警历史

**文件**: `DATA_ROOT/raw/alerts.json`；Postgres 使用 `alerts` 与 `audit_logs`。以下为旧记录结构示意，新预警有业务日、区域、风险/规则版本和来源。默认连续2个有效日确认、5分滞回、有效指标权重覆盖率至少50%；缺测/过期/合成/手工输入不触发。查询不写预警，流水线持久化正式日指标后检查。确认操作幂等并记录确认人/UTC时间，不再裁剪为100条。

```json
[
  {
    "id": "alert_20260115103000",
    "level": "orange",
    "label": "橙色预警",
    "color": "#d29922",
    "risk_score": 65.3,
    "triggered_at": "2026-01-15T10:30:00",
    "acknowledged": false
  }
]
```

---

## 8. Neo4j 历史图数据库 Schema（可选）

以下为旧原型关系类型，保留用于历史对照，不表示已验证的合作、冲突或因果事实。正式关系必须携带证据、来源和日期；共现只能标为共现。种子写入明确标记 demo，正式 NetworkX 从所选窗口内 live/existing 文章证据构图；种子图不进入正式指标。Neo4j 参数、证据域已有模拟回归，尚无真实连接验收，不能将旧演示图作正式证据。

### 节点类型

| 标签 | 属性 | 说明 |
|------|------|------|
| Country | name, region | 国家 |
| Organization | name, aliases | 组织 |
| Person | name, role | 人物 |
| Location | name, type | 地点 |
| NewsEvent | name, date, source | 新闻事件 |
| EventType | name | 事件类型 |

### 关系类型

| 关系 | 方向 | 属性 | 说明 |
|------|------|------|------|
| CONFLICT_WITH | A→B | since | 冲突关系 |
| COOPERATE_WITH | A→B | | 合作关系 |
| MEMBER_OF | A→B | | 成员关系 |
| SANCTIONS | A→B | since | 制裁关系 |
| OPERATES_IN | A→B | | 活动区域 |
| MENTIONS_LOCATION | A→B | | 提及地点 |
| MENTIONS_ORGANIZATION | A→B | | 提及组织 |
| MENTIONS_PERSON | A→B | | 提及人物 |
| INVESTS_IN | A→B | | 投资关系 |
| TRIGGERED | A→B | | 触发关系 |
| CAUSES | A→B | | 因果关系 |

---

## 9. 数据更新策略

| 数据类型 | 更新频率 | 缓存有效期 | 过期处理 |
|----------|----------|-----------|---------|
| 新闻文本 | worker 显式调度 | 保留原始资料 | 按所选日历窗口查询，不自动删除 |
| GDELT 事件 | 连续批次增量 | 累积保留 | 缺口停止推进，重试不重复入库 |
| 夜光代理 | 仅显式采集任务 | 7天缓存 | 正式视图不以代理补真实观测 |
| 经济指标 | 仅显式采集任务 | 30天缓存 | 保留年度粒度，过期明确标记 |
| 风险评分 | 文章发布日重算 | 保留全部分析与版本 | 正式快照幂等，不混入manual/demo/legacy |
| 预警记录 | 正式日指标提交后 | 保留全部历史 | 连续确认、滞回、幂等与确认审计 |

---

## 10. 数据可信度标记规范

| 标记 | 含义 | 示例 |
|------|------|------|
| 官方 | API/权威数据源直接获取 | World Bank GDP 数据 |
| 估算 | 基于有限数据模型推断 | 夜光代理指标、难民估算 |
| 线性插补 | 显式估算视图中的内部缺测插补 | 不改零值、不外推端点，不增加独立样本数 |
| 合成 | 仅 demo 隔离空间 | 正式视图不使用降级合成序列 |
| 缺失/过期 | 没有有效观测或超过观测窗口 | null 或 stale，不能解释为低风险 |
