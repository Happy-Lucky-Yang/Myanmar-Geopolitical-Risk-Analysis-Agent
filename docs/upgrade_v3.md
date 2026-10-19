# 系统升级说明 · 九指标、多源面板与文献知识库

## 本轮多源升级：risk-v4 独立月／年度面板

### 指标与来源分工
| 指标 | 主来源 | 暂时拿不到时的处理 |
|---|---|---|
| 中缅、周边、西方、中美博弈 | GDELT | 无有效配对留缺测，不把无报道解释为关系良好 |
| 冲突频次、事件严重度 | ACLED | 未授权时可显式选GDELT CAMEO计数／规则烈度代理，不能称死亡人数测度 |
| 夜光同比 | VIIRS真实月度分区统计 | 本轮不联网下载；WB电力只作年度背景代理，不复制到月度／邦省 |
| 流离失所 | IDMC GIDD授权下载文件 | 没有文件/连续同版存量则缺测，流量次数与存量变化不互换 |
| 国际制裁 | OFAC法律措施 | 未核验生效日期或μ未审定时不合成；CAMEO仅为显式胁迫新闻代理，欧盟暂未接入 |

- `risk-v4-multisource`为新正式来源模式，`risk-v4-proxy`为显式代理模式。两者均有独立月/年周期、参数与基准版本；旧risk-v2日序列、既有risk-v3实验计算保持原版本。
- ACLED保留Battles、Explosions/Remote violence、Violence against civilians；CF计事件，SE取λ×ln(1+死亡人数)均值。无冲突或死亡缺报不以0伪造严重度；λ、μ必须带`approved_version`，无默认3∶2∶1。
- GDELT按国家对报道量加权，再按国家对象等权。对象缺失保留部分原值，正式合成不接受不完整国家集合；参考基准签名包含实际对象集合，禁止随对象变化拼接比较。
- 正式分要求九项有效且参数、参考基准完整，缺项总分为null。代理分仅按有效权重归一，显示实际覆盖和贡献；不是正式预警。
- Min-Max只用显式冻结的同频参考面板；常量列、不同源／版本／区域、缺基准均不强行标准化。参考范围、输入摘要和参数版本随基准保存。
- VIIRS月度同比只用同产品的t与t-12，基期0/缺失不能算；全年亮度只有12个完整可比月才汇总。VNP46A2是日产品，VNP46A3是月产品。
- IDMC的IDP年末人口存量和Internal displacements流量次数独立保存；存量差可为负，不称唯一新增人数。当前公开年度全国文件不生成省/月值；人口归一与特殊分区文件仍需有对应真实数据后扩展。
- OFAC按法律行动／措施去重，不把一个行动涉及的多名对象重复计数；当前SDN名单只核验对象状态。官方页面检查结果保留`review_required`，不能用发布日期或首次抓取日冒充措施生效日。

### 操作与文件契约
新增代码：`utils/risk_contract.py`、`data/risk_sources.py`、`analyzer/risk_panels.py`、`analyzer/event_links.py`、`storage/risk_store.py`、`risk_api.py`、`scripts/manage_risk_data.py`。

- 统一导入JSON结构为`{source, manifest, records}`。清单必需字段：`start`、`end`（排他）、`region`、`complete`、`dataset_version`、`license`、官方HTTPS `url`。不能夹带密码/token。文件批次摘要、首次取得时间及许可保存在私密运行数据中。
- ACLED records为官方字段，包括event_id_cnty/event_date/event_type/country/fatalities与精度字段；admin1_mapping/actor_mapping仅接受显式核验的映射。
- IDMC当前支持标准列`ISO3`、`Year`、`Conflict Stock Displacement`、`Conflict Internal Displacements`；未知官方版本/特殊分区表明确拒绝，不猜列；官方真实文件仍待团队提供验证。
- VIIRS records沿用完整月观测契约，indicator=nightlight、source=viirs、quality=observed、unit=nW/cm²/sr、产品与版本明确；不接裸影像的自动分区统计。
- OFAC措施记录需action_id/effective_date/program/change/measure_type/url/reviewed；同稿中的对象数组不计数。未知生效日保存待核验，不能计入正式强度。
- WB年度观测可经统一JSON导入：source=worldbank、indicator=electricity_access、quality=proxy、unit=%。旧爬虫返回的年度原值只是背景代理，不代表已导入或VIIRS到位。

```powershell
# 均为预演；不自动复制文件、抓取或写入真实历史
python scripts/manage_risk_data.py import --input 授权批次.json
python scripts/manage_risk_data.py build --frequency monthly --count 12 --parameters 审定参数.json
python scripts/manage_risk_data.py calibrate --input 参考原始面板数组.json --description "团队确认的同频面板基准"
python scripts/manage_risk_data.py build --frequency annual --calibration-id 已保存基准ID
```

写入须额外`--apply --confirm`精确确认DATA_ROOT绝对路径或数据库名，并启用ENABLE_RISK_PANELS。ACLED同步的dry-run不发请求；`inspect-ofac`是显式只读网络检查。无真实授权、原始文件、审定参数和基准前，本轮不执行上述apply。

### 证据核验与界面
- 事件匹配只产候选：地区一致、日期差≤1日、坐标距≤10km、可靠精度及显式相容行为体/类型。原事件ID与版本不合并；match/review CLI均默认预演。
- 原始出处独立性需审核，转载、GDELT索引和ACLED引用同一稿不算独立信源。确认增加证据，不增加CF计数/死亡人数，不输出真伪概率。
- `ENABLE_EVIDENCE_WEIGHTING`默认关闭；只对审核过的独立出处开启KDE≤1.25加权，NumSources仅表示报告的多来源。KDE返回新版本与加权开关。
- 态势总览新增一个默认收起的多源面板，月年/正式代理/基准选择集中显示，不增加主菜单。GET只读已保存面板，不抓取、生成或拟合；新月年不套用旧每日预警/7日预测。
- 修复失租后自动任务仍能入队、hidden被按钮样式覆盖和低对比说明文字；无当前观测为中性灰，长hash折叠。默认地图去省质心光晕、科学KDE显式开启、图例随有效图层显示。

### 云端交接与未验收项
- 迁移`0005_risk_panels`新增risk_source_batches/risk_calibrations/risk_panels/event_links；当前父为0004_workspace，**合并前须部署负责人核对实际head**。未迁移时返回503，不自动建表。
- 为防受限ACLED原文经旧events API公开，原始版本批次存独立risk_source_batches；已有events继续承载GDELT，已有daily_risk不塞月年。文件模式仅单机，PostgreSQL模式写失败不回写文件。
- 新来源/面板/证据开关全部默认关闭；新增任务类型risk_panels复用既有独立worker、租约事务、管理员身份。需要Web/worker一致配置。source sync/import当前由授权CLI管理，不在Web GET或每个worker自动调度。
- Dockerfile、compose.yaml、deploy、现有端口、真实.env和历史迁移均未修改。Compose不自动转发宿主.env新增变量，部署负责人须通过批准的override注入。
- 本轮未申请ACLED账号、未获取IDMC真实文件、未下载VIIRS、未核验完整OFAC措施历史、未审定λ/μ/基准、未执行业务库迁移或历史重算。属于代码与合成夹具验收，不宣称完整九指标已上线。

---

> **口径更正**：本文记录九指标代理算法和四模块的已有实现，不代表已完整执行组长终稿。
> 正式日风险仍由 `pipeline.py` → `compute_daily_risk()` 生成 **risk-v2 五维**；
> **risk-v3 九指标是 `/api/gdelt` 的另一条实验代理路径**，尚未贯通正式趋势、诊断及报告。
> 本轮成功只读解析九指标和四模块终稿后发现测度差异，用户选择先实现产品功能、暂不改指标体系。
> 下文算法描述属于实现事实，不是理论验收结论；此前“v2仅作历史对照、全系统切到v3”的说明撤回。
> 历史回归 343 passed / 9 skipped 仅为此前基线，当前结果与待验收项见 [acceptance.md](acceptance.md)。

## 0. 终稿对标与当前交付边界

| 原文要求 | 输入／周期 | 现有实现与入口 | 页面／测试证据 | 结论与缺口 |
|---|---|---|---|---|
| 冲突频次按 ACLED 事件计数 | 邦省、月／年 | `geo_indicators.py` 用 CAMEO 分类占比；`/api/gdelt` | 综合态势数据接口；`test_geo_indicators.py` | 近似代理，非终稿数据与分母 |
| 严重度为死亡人数对数加权均值 | ACLED 死亡人数 | 现有事件规则烈度 | 地图区域烈度；`test_spatial_multimodal.py` | 非同一测度，不可称正式死亡风险 |
| OFAC／欧盟新增制裁措施 | 真实措施计数 | CAMEO 17 类事件代理 | GDELT 指标接口；派生指标测试 | 尚未接真实制裁措施 |
| 邦省面板 Min-Max 标准化 | 月／年度可比较面板 | 固定范围映射与上限 | 九指标计算测试 | 未实现终稿面板体系 |
| 关系按 Goldstein 配对 | 国家对事件 | 报道量加权净值 | 关系图谱；`test_actor_network.py` | 聚合细节仍须结合新闻编码规则复核 |
| 合作／冲突按正负事件分别分层 | 同一国家对可同时两层 | 当前先净值再分层，可能抵消 | `/graph` 行为体视图 | 部分实现，尚不等于终稿 |
| 夜光、流离失所真实观测 | VIIRS／真实人道数据 | 正式流程拒绝 WB 代理冒充 | 多模态；缺测测试 | 真实观测覆盖不足，不补零 |
| 位势、空间检验及诊断 | 战略中心、边界与同版指标 | 已有权重／空间／Shapley算法 | 位势、空间、诊断测试 | 算法断言不等于现实效果；权重表、待补说明和新闻编码原文仍待逐项核验 |

本轮不调整上述权重、公式或真实历史。文献元数据用于说明理论依据，不自动消除这些差距。

### 产品功能与部署交接
- 新页面：`/literature` 文献中心、`/guide` 使用说明、`/workspace` 个人工作区；仪表盘加入 AI 解读区。
- 新增 `product_api.py`、`workspace_api.py` 独立 Blueprint；账户复用既有安全模块和管理员 CLI。
- `ENABLE_LITERATURE`、`ENABLE_WORKSPACE`、`ENABLE_RISK_BRIEFING`、`ENABLE_AUTO_BRIEFING`、`ENABLE_UN_NEWS` 均默认 `false`。关闭时返回明确状态，不影响旧业务。
- 多人工作区要求现有 PostgreSQL 共享模式；新增迁移 `0004_workspace`，父版本 `0003_observation_versions`。合并前与部署分支核对 head；本轮不执行真实迁移，不修改冻结 schema、连接串、Compose 或部署脚本。
- 部署负责人确认后，在授权数据库依次执行原有迁移至 `0003`、新增 `0004`，再启用共享账户和工作区；Web 与独立 worker 使用一致开关。`assistant` 任务由已有 worker 消费，队列只存请求 ID，个人正文留在所有者隔离表。
- 手动每日每用户最多20个任务，自动每日全局最多5个、同地区冷却6小时。额度按任务在事务中预留，最多一次生成加一次格式修复；失败不返还预留，不自动重试超时调用，因此实际请求上界分别为40和10次。删除会话不重置额度。
- 自动任务只在正式流水线持久化后检查；前后7日各至少6个有效日、最新不超过2日、覆盖与权重可比且达到变化门槛。没有达到条件仅记录原因。
- AI 当前采用固定的风险历史、新闻、文献只读检索，不开放任意工具执行。用户通过筛选明确时间地区；没有自然语言时间解析器。引用、结构和数值校验只能拦截部分错误，不能证明语义正确或替代人工核验。
- 文献默认20篇分页、最多100篇；只展示公开字段。DOI不等于免费下载，只有带审核及许可的开放全文链接才显示全文入口；不提供私密PDF服务。
- UN News 已只读核验一篇真实页面及 robots：正文使用 `#block-views-block-content-fields-block-body .field--name-field-text-column`；新闻日期与meta发布时间可不同，优先页面新闻日期并保留冲突标记。未启动历史抓取或生产调度。
- 新增测试分别覆盖产品契约、工作区权限、PostGIS事务和离线浏览器；未配置测试库时只能标记待联调，不宣称云端就绪。

---

## 1. 九指标三层风险评价体系（risk-v3）

### 1.1 权重结构

要素层按 **地缘关系 0.6 / 地理环境 0.2 / 地缘结构 0.2**（比 3:1:1，源自胡志丁等 2013 三要素框架
+ 2014 权力等级论，各层判断矩阵一致性比率 CR=0），逐级归一到九项全局权重（`config.yaml` `risk_weights_v3`，和=1.0）：

| 指标（键） | 中文 | 全局权重 | 方向 | 测度来源 |
|-----------|------|---------|------|---------|
| conflict_frequency | 冲突频次 | 0.162 | 正向 | CAMEO 18/19/20 类事件占比 |
| china_myanmar_relation | 中缅关系 | 0.138 | 负向 | GDELT Goldstein 按报道量加权净值 |
| china_us_rivalry | 中美博弈 | 0.133 | 正向 | 中美同现的冲突型强度 |
| event_severity | 事件严重度 | 0.120 | 正向 | CAMEO 烈度代理（非死亡人数口径） |
| nightlight_change | 夜光变化 | 0.110 | 负向 | World Bank 电力代理（非 VIIRS） |
| neighbor_relation | 周边邻国关系 | 0.102 | 负向 | 缅-泰/印/孟/老 Goldstein 净值 |
| displacement | 流离失所 | 0.090 | 正向 | IDMC/IDP（当前占位） |
| western_relation | 西方(美缅)关系 | 0.078 | 负向 | 缅-美/欧 Goldstein 净值 |
| intl_sanction | 国际制裁 | 0.067 | 正向 | CAMEO 17 且对方为大国/国际组织 |

> “正向”= 值越大风险越高；“负向”= 关系越敌对（Goldstein 净值越低）风险越高，派生时已统一方向，
> 使九指标对风险分**同向单调**，可直接加权合成。

### 1.2 GDELT 派生指标层（`analyzer/geo_indicators.py`）

五项双边/结构指标从 GDELT 事件派生（P0 已扩解析器捕获 **Goldstein / NumMentions / Actor1CountryCode /
Actor2CountryCode** 四列）：

- **关系类净值**：对某国家对的全部共同事件，按报道量加权求 Goldstein 净值 `R = Σ(g×m)/Σm ∈ [-10,10]`，
  再线性映射到 [0,1] 风险贡献（越敌对越高）。中缅 / 周边邻国（泰印孟老）/ 西方（美欧）三类同口径。
- **中美博弈**：中美同现事件的冲突型强度（CAMEO 冲突码占比 × 烈度）。
- **冲突频次 / 事件严重度 / 国际制裁**：由 CAMEO 事件码分类聚合。
- **国家码归一**：`canon_country()` 统一 FIPS/ISO 别名（BM→MMR、CH→CHN、US→USA、EU→EUU…）。
- `to_scorer_input()` 输出 `(values, quality)`：每个指标带 `observed / derived / missing` 质量标记，
  稀疏缺测（如某窗口无制裁事件）标记为 missing，**不补零**。

### 1.3 合成与动态归一（`analyzer/risk_scorer.py`）

`GeoRiskScorer.compute_event_risk(events, external_data)` → `algorithm_version="risk-v3"`：

1. `compute_geo_indicators()` 派生九指标 → `to_scorer_input()` 得 values/quality；
2. 复用 v2 的**动态权重归一**：`available = {k:w | 值有限 且 quality∈{observed,derived}}`，
   `coverage = Σ available`，权重按 available 归一，value clamp 到 [0,1]；
3. `score = round(Σ(value×weight) × 100, 2)`，缺测指标 contribution=None（不计入、不补零）；
4. 返回体附带 `indicator_model`、`geo_indicators`、`event_count`、`coverage`、`status`。

> **完美适配九指标稀疏缺测**：缅甸事件稀疏，双边指标常缺测；动态归一保证“缺测不稀释、不伪造”，
> 与 §2.3 数据诚信契约一致。`/api/gdelt` 已挂载 `metrics['geo_risk']`。

---

## 2. 智能计算四模块对齐

### 2.1 模块一 · 地缘位势（`analyzer/geo_potential.py`，potential-v3）

- **战略中心权重按 3:2:1 归一**（组长表1，一档:二档:三档）：皎漂港 0.30、瑞丽(中缅边境) 0.30、
  内比都 0.20、妙瓦底(泰缅边境) 0.10、仰光 0.10，合计 1.00。`validate_center_weights()` 断言权重和为 1（容差 1e-6）。
- **距离**：球面大圆距离 haversine（R=6371km），省邦取面积加权质心。
- **距离敏感性**：`distance_sensitivity()` 比较基准(大圆)与对照(陆路通行距离)两套排序的 Spearman ρ；
  ρ≥0.8 判定“不敏感”，只报基准；无路网数据时返回 `status="unavailable"`，**不臆造对照距离**。
- **三断言**：(1) 权重和为 1；(2) 位势非负、且风险为 0 时位势为 0；(3) 靠中国边境通道/印度洋出海口
  省邦位势高于内陆（人工确认，测试以等风险下边境>内陆验证）。

### 2.2 模块二 · 空间自相关（`analyzer/spatial_analysis.py`，moran-v3 / lisa-v1 / gistar-v1）

- **三套空间权重矩阵**（均行标准化，和为 1，容差 1e-6）：Queen 邻接（含共点）、距离阈值
  （质心距 ≤ 最近邻距离中位数）、距离衰减（w=1/d²）。
- **全局 Moran's I**：`morans_i()`，N=999 固定种子(42) **单侧**置换检验（正空间自相关假设），
  报 I / 期望 −1/(n−1) / p 值；≥8 个有效区域才报显著性，零方差或无邻接不报。
- **LISA 局部 Moran**：`lisa()`，`I(i)=z(i)·Σ_j w(i,j)z(j)`，四分类 HH(热点)/LL(冷点)/HL(孤立高值)/LH(低值包围高值)，
  置换 p 值 + 显著性着色（不显著灰显）。
- **Getis-Ord Gi\***：`getis_ord_gi_star()`，>1.96 显著热点、<−1.96 显著冷点。
- **三矩阵敏感性报告**：`spatial_autocorrelation_report()`，primary=queen，报告三矩阵方向是否一致(direction_consistent)。
- **三断言**：(1) 行标准化后每行和为 1；(2) 固定种子可复现；(3) **输入整体逆序时全局 Moran's I 不变**（自检，容差 1e-9）。

### 2.3 模块三 · 关系网络（`analyzer/network_analyzer.py`，actor-network-v1）

- **固定八节点**：MMR(案例国) / CHN(主要外部行为体→中缅关系) / USA(域外大国→西方关系) /
  EUU(欧盟，单一集体行为体→西方关系) / THA / IND / BGD / LAO(陆上邻国→周边邻国关系)，ISO 3166 三字母编码。
- **有向加权**：对每一有序对 (Actor1→Actor2)，取共同 GDELT 事件按报道量加权 Goldstein 净值
  `R=Σ(g×m)/Σm ∈[-10,10]`（与九指标关系类同口径）。
- **分层多层网络**：合作层(R>0) / 冲突层(R<0)；重点指标中介中心性（枢纽地位）+ 社区模块度。
- **四断言**：(1) 节点数为 8 且编码一致；(2) 边权∈[-10,10]；(3) 无自环；(4) 模块度∈[-0.5,1]。
- **输出不进入风险指数合成**（见 §3 职责边界①）。

### 2.4 模块四 · 诊断性归因（`analyzer/diagnostic.py`，shapley-v1）

- **Shapley 精确分解**：`shapley_values(weights, zvalues)`，`v(S)=Σ_{k∈S} w_k·Z_k / Σ_{k∈S} w_k`，
  n=9 时枚举 2⁹=512 子集（≤2304 次求值，**精确无近似**），`φ(k)=Σ_S [|S|!(n−|S|−1)!/n!]·[v(S∪{k})−v(S)]`。
- **三恒等式断言**（容差 1e-6）：**A1** ΣC(k)=1（贡献份额归一）；**A2** Σw_k·ΔZ_k=ΔR（变化分解残差为零）；
  **A3** Σφ(k)=R=v(N)（Shapley 有效性）。`shapley_attribution()` 校验 A1/A3，`change_identity()` 校验 A2。
- **措辞约束**：归因文本限“贡献份额为百分之几”，`assert_no_causal_verbs()` 过滤因果动词
  “导致/引起/造成”，命中即报错——诊断输出是**贡献分解，不是因果结论**。
- `INDICATOR_META` 已扩至九指标；`diagnose()` / `diagnose_change()` 追加式挂载 shapley/change_identity，不改既有键。

---

## 3. 三处职责边界（代码写定 + 文档注明）

1. **关系网络输出不进入风险合成**：关系层五项指标（中缅/中美/周边/西方/制裁）已以双边配对进入九指标合成，
   行为体网络再入将**重复计量**；`build_actor_network()` 元数据 `enters_risk_synthesis=False`。
2. **知识图谱实体网络 vs 行为体关系网络分属两用途**：`build_article_graph()`（文章级实体共现，network-v2）
   与 `build_actor_network()`（八节点国家行为体，actor-network-v1）**不复用同一节点表**，语义不同（共现≠合作/冲突）。
3. **归因文本不用因果动词**：见 §2.4，模板层关键词过滤。

---

## 4. 文献知识库一期（元数据索引层，`analyzer/knowledge_base.py`，kb-index-v1）

### 4.1 数据源与合规

- 源表：组长团队《缅甸地缘文献库·区域国别地缘环境计算·文献总表.xlsx》——**645 篇去重题录**（原始 822 文档，
  同文多副本合并），含 19 列（序号/类别/子类/题名/题名中文/作者/机构/年份/期刊/摘要/关键词/DOI/来源库/
  相关性/是否核心/研究主题类别/判断理由/备注/知识库文件标题）；另有“候选核心文献”84 篇、“统计与说明”。
- 元数据 provenance：源表逐篇解析文献首页/前 3 页，扫描件经 OCR，**正文未出现的信息一律留空、未作推断**。
- **合规边界**：源表属团队私密资料，位于仓库外，**不入库**；路径经环境变量 `KB_SOURCE_XLSX`（写在 gitignored 的
  `.env`）提供，**不写死进开源的 config.yaml**。派生索引缓存落 `DATA_ROOT/knowledge_base/literature_index.json`
  （已 gitignore）。当前 DATA_ROOT 指向仓库外私密运行目录，索引天然不入仓库。

### 4.2 能力

- **stdlib xlsx 解析**：环境无 openpyxl，`read_xlsx()` 用 `zipfile`+`ElementTree` 解析（含 sharedStrings 富文本、
  inlineStr），并带 **DTD/实体防护**（拒绝含 `<!DOCTYPE`/`<!ENTITY` 的 XML，防 XXE/实体膨胀）。
- **结构化 + 清洗**：19 列规范化；作者/关键词/主题按分号切分；**脏主题标签清洗**（去“12 ”“15 ”编号前缀，
  合并为 15 个规范主题）；年份正则提取；is_core 派生。
- **主题→四模块/九指标映射**：源表“研究主题类别”与智能计算四模块**精确 1:1**——地缘位势与影响力测算→模块一、
  空间统计与空间自相关→模块二、地缘关系网络分析→模块三、地缘风险·脆弱性与归因→模块四；其余主题映射到九指标。
- **检索**：字段加权 TF（题名5/译名4/关键词4/主题2.5/摘要2/作者2/期刊1）+ jieba 分词（缺失则 CJK 二字gram 兜底）
  + facet 过滤（主题/类别/模块/指标/相关性/核心/年份）。
- **支撑文献召回**：`relevant_literature(module|indicator|topic)` 把文科文献接到技术模块——真实数据上四模块覆盖
  diagnostic 31 / network 23 / geo_potential 17 / spatial 16 篇，九指标全覆盖（event_severity 160 … nightlight_change 46）。
- **API**：`/api/knowledge_base/status|search|relevant|record/<id>`；源表缺失时优雅降级（空索引 + status，不 500）。
- **构建脚本**：`python scripts/build_knowledge_base.py [--source X.xlsx] [--print-facets]`。

### 4.3 二期展望（全文层）

一期仅元数据（题录+摘要+关键词）。PDF 全文到位后二期接入：按 `kb_file` 关联 PDF → 全文切块 + 向量/关键词检索 →
RAG 式“指标/模块 → 支撑文献原文段落”溯源，服务研究报告与答辩引用。

---

## 5. 前后对比

| 维度 | 五维正式路径及早期实现 | 新增算法／实验能力（非正式路径替换） |
|------|------------------|------------------|
| 风险指标 | 5 维（冲突/情感/夜光/难民/严重度） | **9 指标三层**（要素 0.6/0.2/0.2） |
| 双边关系 | 仅设计、暂缓（未入模型） | **中缅/中美/周边/西方/制裁 5 项 GDELT 派生入合成** |
| 地缘位势 | 1/d² + 5 中心（权重未归一断言） | **3:2:1 归一 + 3 断言 + Spearman 距离敏感性** |
| 空间自相关 | 全局 Moran's I（单权重矩阵） | **+ LISA 四分类 + Gi\* + 三权重矩阵敏感性 + 逆序自检** |
| 关系网络 | 实体共现网络（network-v2） | **+ 八节点有向加权行为体网络（分层/中心性/模块度）** |
| 诊断归因 | 贡献度前后期比较 | **+ Shapley 精确分解 + A1/A2/A3 恒等式 + 因果动词守卫** |
| 文献支撑 | 无 | **645 篇元数据索引 + 主题→模块/指标映射 + 检索/召回 API** |

---

## 6. 涉及文件

- 新增：`analyzer/geo_indicators.py`、`analyzer/knowledge_base.py`、`scripts/build_knowledge_base.py`、
  `tests/test_geo_indicators.py`、`tests/test_geo_potential.py`、`tests/test_actor_network.py`、`tests/test_knowledge_base.py`
- 升级：`analyzer/risk_scorer.py`(risk-v3)、`analyzer/geo_potential.py`(potential-v3)、
  `analyzer/spatial_analysis.py`(moran-v3/lisa-v1/gistar-v1)、`analyzer/network_analyzer.py`(actor-network-v1)、
  `analyzer/diagnostic.py`(shapley-v1)、`app.py`(/api/gdelt geo_risk + /api/knowledge_base/*)、
  `utils/config.py`(get_risk_weights_v3/get_knowledge_base_config)、`config.yaml`(risk_weights_v3/knowledge_base)
- 扩充测试：`tests/test_spatial_multimodal.py`(+5)、`tests/test_diagnostic_contracts.py`(+6)

---

## 7. 测试与复现

```bash
python -m pytest -q                                   # 343 passed, 9 skipped
python scripts/build_knowledge_base.py --source <文献总表.xlsx>   # 生成 645 篇索引
```

新增/扩充契约测试覆盖：九指标派生与 risk-v3 端到端（`test_geo_indicators.py`）、地缘位势 3:2:1 与距离敏感性
（`test_geo_potential.py`）、三权重矩阵/LISA/Gi\*/逆序不变（`test_spatial_multimodal.py`）、Shapley 手算/A1A2A3/
因果过滤（`test_diagnostic_contracts.py`）、八节点/边权/无自环/模块度（`test_actor_network.py`）、xlsx 解析/清洗/
映射/检索/安全/降级/API（`test_knowledge_base.py`）。所有算法版本号随结果返回，可一键复跑核验。
