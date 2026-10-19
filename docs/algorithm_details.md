# 算法实现细节文档

本文描述正式数据域与独立实验算法，二者不能混称。正式日风险仍为 risk-v2，risk-v3 为 GDELT 九指标代理路径；`Legacy*` 类保留历史对照。固定夹具只验证数学和数据边界，不代表真实预测准确率。年度观测分面已在 dashboard 接入；NER 留出集、真实 PostGIS 集成及部署验收尚未完成。

## 多源九指标（risk-v4）与旧版边界
- 新代码`analyzer/risk_panels.py`按完整月或年计算，来源分工为GDELT关系四项、ACLED频次/烈度、VIIRS夜光、IDMC存量变化、OFAC措施。
- CF为ACLED武装事件计数；SE=Σλ类型×ln(1+死亡人数)/N；λ未审定或死亡缺报不算正式烈度。
- 关系先按国家对NumMentions加权，再对对象等权；对象缺失为partial，不进入正式完整分，实际对象集合进入基准签名。
- VIIRS同比(t,t-12)必须同产品且基期>0；年度需12个完整月。IDMC连续存量差可为负，不能混成流量次数；WB电力仅为年度代理。
- 制裁按新生效法律措施去重后Σμ，不以SDN对象数计量；目前仅OFAC，欧盟尚未接入。
- 同频冻结Min-Max参考面板记录范围/源/参数/hash，常量列不给0。正式总分仅九项、审定参数与基准齐备才返回；代理只按有效指标动态归一并单独标注。
- 新月年面板不进入daily_risk，不套用旧7日预测/每日预警。详细操作与数据缺口见`docs/upgrade_v3.md`。

## 1. 风险评分模型（risk-v3 九指标）

> 本节为 `algorithm_version="risk-v3"` 的独立九指标代理算法，并未替换 `risk-v2` 正式日系列。
> 与组长终稿的 ACLED、OFAC、月年度标准化等测度仍有差异；本轮按用户确认暂不改体系。
> 实现与终稿逐项对照见 [系统升级说明](upgrade_v3.md#0-终稿对标与当前交付边界)。

### 1.1 三层权重结构

要素层按**地缘关系 0.6 / 地理环境 0.2 / 地缘结构 0.2**（比 3:1:1，源自胡志丁等 2013 三要素框架 + 2014
权力等级论，各层判断矩阵一致性比率 CR=0）逐级归一到九项全局权重（`config.yaml` `risk_weights_v3`，和=1.0）：

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

“正向”= 值越大风险越高；“负向”= 关系越敌对（Goldstein 净值越低）风险越高。派生时已统一方向，使九指标对风险分**同向单调**，可直接加权合成。

### 1.2 GDELT 派生指标层（`analyzer/geo_indicators.py`）

五项双边/结构指标从 GDELT 事件派生（解析器捕获 **Goldstein / NumMentions / Actor1CountryCode / Actor2CountryCode** 四列）：

- **关系类净值**：对某国家对的全部共同事件，按报道量加权求 Goldstein 净值
  $R = \frac{\sum_i g_i m_i}{\sum_i m_i} \in [-10,10]$，再线性映射到 [0,1] 风险贡献（越敌对越高）。中缅 / 周边邻国（泰印孟老）/ 西方（美欧）三类同口径。
- **中美博弈**：中美同现事件的冲突型强度（CAMEO 冲突码占比 × 烈度）。
- **冲突频次 / 事件严重度 / 国际制裁**：由 CAMEO 事件码分类聚合。
- **国家码归一**：`canon_country()` 统一 FIPS/ISO 别名（BM→MMR、CH→CHN、US→USA、EU→EUU…）。
- `to_scorer_input()` 输出 `(values, quality)`：每个指标带 `observed / derived / missing` 质量标记，稀疏缺测（如某窗口无制裁事件）标记为 missing，**不补零**。

### 1.3 动态权重归一化

`null`、非有限值及 `quality==missing` 的指标不参与评分；真实 `0` 仍是有效值。有效指标权重归一化：

$$w'_i = \frac{w_i}{\sum_{j \in available} w_j}$$

其中 $available$ 为值有限且 `quality ∈ {observed, derived}` 的指标集合（含零值）。`coverage = Σ available` 即原始有效权重之和，随结果返回；它不是新闻采集完整率。缺测指标 contribution=None，不计入、不补零。

### 1.4 综合评分公式

$$RiskScore = \mathrm{round}\left(100 \times \sum_{i \in available} w'_i \cdot v_i,\ 2\right)$$

$v_i$ 已 clamp 到 [0,1]。结果为 [0,100] 风险分，不是发生概率；无任何有效指标则为 null，不能报零风险。正式日指标按去重文章最早发布业务日统一重算，手工输入不更新共享日指标。返回体附带 `indicator_model`、`geo_indicators`、`event_count`、`coverage`、`status`；`/api/gdelt` 已挂载 `metrics['geo_risk']`。

### 1.5 风险等级划分

| 分数范围 | 等级 | 颜色 |
|----------|------|------|
| ≥ 70 | 高风险 | 红色 (#f85149) |
| ≥ 40 | 中风险 | 橙色 (#d29922) |
| < 40 | 低风险 | 绿色 (#3fb950) |

---

## 2. 情感分析

### 2.1 多语言策略

| 语言 | 工具 | 说明 |
|------|------|------|
| 中文 | SnowNLP | `snownlp.SnowNLP(text).sentiments` → 0-1 (越大越正面) |
| 英文 | VADER (NLTK) | `SentimentIntensityAnalyzer.compound` → [-1, 1] |
| GDELT | tone 字段 | 直接使用 GDELT 预计算 tone 值 |

### 2.2 融合策略

```python
if gdelt_tone is not None:
    risk_score = 0.5 - gdelt_tone / 20  # tone: [-10,10] → risk: [0,1]
elif lang == "zh":
    risk_score = 1.0 - snownlp_score    # 正面度 → 风险度 (反向)
elif lang == "en":
    risk_score = 0.5 - vader_compound / 2
else:
    risk_score = 0.5  # 默认
```

### 2.3 冲突关键词的评分位置

冲突关键词计入 conflict_frequency 和 event_severity，不直接重复叠加到
sentiment_avg，避免同一风险信号被双重计分。

---

## 3. 趋势分析

### 3.1 移动平均

窗口大小: 7天 (可配置)

$$MA_t = \frac{1}{w}\sum_{i=t-w+1}^{t} x_i$$

### 3.2 线性回归

使用最小二乘法拟合趋势线:

$$\hat{y} = \beta_0 + \beta_1 x$$

自变量 $x$ 是距窗口起点的实际日历天数，不是有效样本序号。至少14个有效日且窗口覆盖率≥80%才拟合；缺测不补零。API 先生成连续日历序列，移动平均窗口内有效比例也须≥80%。

其中:
- $\beta_1 = \frac{n\sum x_i y_i - \sum x_i \sum y_i}{n\sum x_i^2 - (\sum x_i)^2}$
- $\beta_0 = \bar{y} - \beta_1 \bar{x}$

### 3.3 趋势方向判断

| 斜率 $\beta_1$ | 趋势 |
|----------------|------|
| > 0.005 | 上升 |
| < -0.005 | 下降 |
| 其他 | 平稳 |

### 3.4 决定系数 $R^2$

$$R^2 = 1 - \frac{\sum(y_i - \hat{y}_i)^2}{\sum(y_i - \bar{y})^2}$$

$R^2$ 仅描述样本内拟合优度，不是显著性检验或预测置信度；零方差时为 null。

### 3.5 异常检测（历史窗口中位数/MAD）

仅使用当前点之前28个日历槽中的有效观测；至少14个历史有效值。尺度为 `max(1, 1.4826×MAD)`，绝对偏差/尺度大于3.5才标记。缺测及样本不足不判异常，不使用未来数据估计基线。

---

## 4. 预测模型

### 4.1 探索性外推门槛

至少14个有效日、窗口覆盖率≥80%，且截止日有真实观测。未满足时返回空预测与原因。默认7日外推，最多30日；结果截断至0～100。

### 4.2 基线比较与滚动回测

候选为末值 `last`、时间间隔修正的 EWMA 和真实日期线性回归。至少56个有效日且完成≥4个七日回测窗口后，按 MAE、RMSE 选择模型；不足则保留末值基线。不以复杂度或样本内 R² 宣称升级有效。

回测使用扩展训练窗，每次向后移7天；训练窗至少14日且覆盖≥80%，测试窗至少6个有效日。模型选择与误差估计共用滚动样本，是探索性回测，不是独立留出集验收。

### 4.3 残差经验区间

回测达到门槛且预测期≤7日时，使用所选模型滚动残差10%与90%分位数构建经验区间。它不保证80%或95%的概率覆盖；更长预测期不外推此区间。

---

## 5. 夜间灯光遥感指标

### 5.1 数据源

旧采集器保留以下 World Bank 电力代理供历史/估算研究，正式评分及多模态不使用它作为遥感观测：
- `EG.ELC.ACCS.ZS`: 电力覆盖率
- `EG.ELC.LOSS.ZS`: 电力传输损耗率

### 5.2 归一化映射

$$nl\_change = 0.1 + 0.8 \times \frac{access\_pct}{100}$$

传输损耗修正:
$$loss\_factor = \frac{25 - loss\_pct}{20}$$

融合:
$$nl\_change = 0.7 \times nl\_change_{access} + 0.3 \times loss\_factor$$

### 5.3 正式观测边界

年度指标保持年度粒度，不能复制或插值为独立月度遥感点。真实 VIIRS 接口等待有来源、单位、产品版本与完整周期的观测；本轮授权目录未发现常见栅格格式，未声称完成遥感处理。

---

## 6. 经济指标归一化

| 指标 | 原始范围 | 映射公式 | 含义 |
|------|----------|---------|------|
| GDP增长率 | [-10%, 15%] | (raw+10)/25 | 经济增长 |
| 通胀率 | [-5%, 30%] | (30-raw)/35 | 高通胀=负面 |
| 贸易占GDP | [0%, 100%] | raw/100 | 经济开放度 |

### 6.1 难民变化估算

以下仅为旧代理估算公式，不是难民统计观测，不参与正式风险或预警：
$$refugee\_change = 0.6 + 0.4 \times economic\_distress$$

其中:
- $GDP < 0$: distress += 0.6 × min(1, |GDP|/10)
- $GDP < 3%$: distress += 0.3
- $inflation > 15%$: distress += 0.4 × min(1, (infl-15)/20)

---

## 7. NetworkX 网络分析

正式网络从窗口内去重文章的实体提及生成，关系为 `CO_OCCURS_IN_ARTICLE`，保留文章 ID、日期、来源/URL 配对和 NER 版本；共现不自动命名为合作、冲突或因果。按最早发布日期筛选，保留最新 NLP 快照。manual/demo/legacy 不混入正式图，Neo4j 为可选存储，种子图单独标记 demo。

### 7.1 中心性指标

- **度中心性**: $C_D(v) = \frac{deg(v)}{n-1}$
- **介数中心性**: $C_B(v) = \sum_{s \neq v \neq t} \frac{\sigma_{st}(v)}{\sigma_{st}}$
- **接近中心性**: $C_C(v) = \frac{n-1}{\sum_{t} d(v,t)}$

### 7.2 社区检测

使用 Louvain 算法 (`networkx.community.louvain_communities`):
- 优化模块度 Q
- 降级方案: 连通分量

### 7.3 行为体关系网络（actor-network-v1）

在文章级实体共现网络之外，`build_actor_network()` 另建**八节点国家行为体网络**（与九指标关系类同口径，但**不复用同一节点表**，语义不同：共现 ≠ 合作/冲突）：

- **固定八节点**：MMR（案例国）/ CHN（主要外部行为体→中缅关系）/ USA（域外大国→西方关系）/ EUU（欧盟，单一集体行为体→西方关系）/ THA / IND / BGD / LAO（陆上邻国→周边邻国关系），ISO 3166 三字母编码。
- **有向加权**：对每一有序对 (Actor1→Actor2)，取共同 GDELT 事件按报道量加权 Goldstein 净值 $R = \frac{\sum g m}{\sum m} \in [-10,10]$。
- **分层多层网络**：合作层（R>0）/ 冲突层（R<0）；重点指标介数中心性（枢纽地位）+ 社区模块度。
- **四断言**：节点数为 8 且编码一致；边权 ∈ [-10,10]；无自环；模块度 ∈ [-0.5,1]。
- **输出不进入风险指数合成**：关系层五项指标已以双边配对进入九指标合成，行为体网络再入将重复计量（元数据 `enters_risk_synthesis=False`）。

---

## 8. 多模态时空对齐

### 8.1 Pearson 相关系数

$$r = \frac{\sum(x_i - \bar{x})(y_i - \bar{y})}{\sqrt{\sum(x_i - \bar{x})^2 \cdot \sum(y_i - \bar{y})^2}}$$

`multimodal-v3` 只用所选窗口内完整月：`period_start` 为月初，`period_end` 为下月月初且排他。尚未结束的月份和窗口边缘残缺月排除，不读取窗口外旧数据回填。

- 夜光必须具有 observed 质量、有限值、来源及非空单位；同ID不同内容为冲突，多来源/版本不平均为一个值。
- 仅相邻完整月且来源、单位、产品、数据集版本一致时计算夜光差分。
- 新闻仅 live/existing；事件读取完整历史后裁剪，按独立事件标识去重。未知类别不产生假零冲突数。
- 每对序列至少12个不重复完整月及不同观测ID、双方非零方差才计算 Pearson。夜光序列变化则拒绝混算，并返回样本数与原因。
- 相关为探索性，未校正时间自相关，不是因果证据。页面分面展示，混合夜光序列只展示带单位原值表，不连成单一曲线。

### 8.2 空间分析与 KDE

事件坐标用 EPSG:4326 边界归属；文字只匹配明确事件发生地，边界歧义列为未定位。省级指标是已定位事件规则烈度均值，非全国日风险投射，无观测灰显。

KDE 使用本地等面积投影、公里带宽；绝对密度以加权事件/平方公里计，跨窗口比较按天归一并用共同带宽/色标。相对密度只用于窗口内位置对比，不能以每帧峰值归一后的颜色比较时间强弱。

**空间自相关（`analyzer/spatial_analysis.py`，moran-v3 / lisa-v1 / gistar-v1）**：提供三套行标准化空间权重矩阵（Queen 邻接含共点 / 距离阈值 ≤ 最近邻距离中位数 / 距离衰减 w=1/d²），每行和为 1（容差 1e-6）。

- **全局 Moran’s I**：$I = \frac{n}{\sum_i\sum_j w_{ij}} \cdot \frac{\sum_i\sum_j w_{ij} z_i z_j}{\sum_i z_i^2}$，N=999 固定种子（42）**单侧**置换检验（正空间自相关假设），报 I / 期望 $-1/(n-1)$ / p 值；至少 8 个有效区域才报显著性，零方差或无邻接不报。**输入整体逆序时 I 不变**（自检断言，容差 1e-9）。
- **LISA 局部 Moran**：$I(i) = z_i \sum_j w_{ij} z_j$，四分类 HH（热点）/ LL（冷点）/ HL（孤立高值）/ LH（低值包围高值），置换 p 值 + 显著性着色（不显著灰显）。
- **Getis-Ord Gi\***：标准化后 >1.96 显著热点、<−1.96 显著冷点。
- **三矩阵敏感性**：`spatial_autocorrelation_report()` primary=queen，报告三矩阵方向是否一致（direction_consistent）。

战略中心权重与空间相关均不是因果结论。

### 8.3 预警与贡献分析

正式预警需要有效、未过期观测，默认连续2日确认、5分滞回及指标权重覆盖≥50%；去重并保留确认人/时间审计。查询不生成新预警。贡献分析使用已保存的实际指标贡献，对比等长前后期，不从当前权重反推旧分数，也不称因果归因。

---

## 9. 链式推理

### 9.1 推理链模板

1. **事件识别** → 分类事件类型、行为体、地点
2. **影响分析** → 基于事件信息评估中缅关系影响
3. **趋势研判** → 短期/中期趋势、升级概率
4. **建议生成** → 政策建议、风险缓解措施

### 9.2 字段完整率与失败边界

每一步按对应 schema 检查字段及类型，返回 `schema_completeness`，不是正确率。调用/解析/字段校验失败时显式标记失败并停止后续步骤。统一使用 LLM 提供商配置、超时和重试；占位密钥不触网，失败不进入成功缓存。缓存隔离提供商、模型、提示词/schema 版本与输入，结论仍须人工依据证据复核。

---

## 10. 地缘位势与诊断归因

### 10.1 地缘位势（`analyzer/geo_potential.py`，potential-v3）

某省地缘位势 $P_i = \left(\sum_{c} \frac{w_c}{d_{ic}^2}\right) \times R_i$，其中 $w_c$ 为战略中心权重、$d_{ic}$ 为省邦质心到中心的球面大圆距离（haversine，R=6371km）、$R_i$ 为风险分。5 个战略中心按 **3:2:1 归一**：皎漂港 0.30、瑞丽 0.30、内比都 0.20、妙瓦底 0.10、仰光 0.10（和=1.0，`validate_center_weights()` 断言，容差 1e-6）。

- **距离敏感性**：`distance_sensitivity()` 比较基准（大圆）与对照（陆路通行距离）两套排序的 Spearman ρ；ρ≥0.8 判“不敏感”只报基准；无路网数据时返回 `status="unavailable"`，**不臆造对照距离**。
- **三断言**：权重和为 1；位势非负且风险为 0 时位势为 0；等风险下靠中国边境通道/印度洋出海口省邦位势高于内陆。

### 10.2 诊断性归因（`analyzer/diagnostic.py`，shapley-v1）

**Shapley 精确分解**：$v(S) = \frac{\sum_{k \in S} w_k Z_k}{\sum_{k \in S} w_k}$，$\varphi(k) = \sum_{S \subseteq N\setminus\{k\}} \frac{|S|!\,(n-|S|-1)!}{n!}\,[v(S\cup\{k\}) - v(S)]$。n=9 时枚举 $2^9=512$ 子集（≤2304 次求值，**精确无近似**）。

**三恒等式断言**（容差 1e-6）：
- **A1** $\sum_k C(k) = 1$（贡献份额归一，`shapley_attribution()` 校验）；
- **A2** $\sum_k w_k \Delta Z_k = \Delta R$（变化分解残差为零，`change_identity()` 校验）；
- **A3** $\sum_k \varphi(k) = R = v(N)$（Shapley 有效性）。

**措辞约束**：归因文本限“贡献份额为百分之几”，`assert_no_causal_verbs()` 过滤因果动词“导致/引起/造成”，命中即报错——诊断输出是**贡献分解，不是因果结论**。`diagnose()` / `diagnose_change()` 以追加式挂载 shapley / change_identity，不改既有键。

---

## 11. 文献知识库（`analyzer/knowledge_base.py`，kb-index-v1）

一期为**元数据索引层**（题录 + 摘要 + 关键词），源表为组长团队《缅甸地缘文献库·文献总表.xlsx》（645 篇去重题录，19 列；另有候选核心 84 篇）。

- **stdlib 解析**：环境无 openpyxl，`read_xlsx()` 用 `zipfile` + `ElementTree` 解析（合并 sharedStrings 富文本与 inlineStr），并带 **DTD/实体防护**（`_safe_fromstring` 拒绝含 `<!DOCTYPE`/`<!ENTITY` 的 XML，防 XXE/实体膨胀）。
- **清洗与派生**：19 列规范化；作者/关键词/主题按分号切分；脏主题标签去编号前缀（`^\s*\d+\s*`）合并为 15 个规范主题；年份正则提取；`is_core` 派生。
- **主题→四模块/九指标映射**：源表“研究主题类别”与智能计算四模块**精确 1:1**（地缘位势与影响力测算→模块一、空间统计与空间自相关→模块二、地缘关系网络分析→模块三、地缘风险·脆弱性与归因→模块四），其余主题映射到九指标。
- **检索**：字段加权 TF（题名5/译名4/关键词4/主题2.5/摘要2/作者2/期刊1）+ jieba 分词（缺失则 CJK 二字 gram 兜底）+ facet 过滤；`relevant_literature(module|indicator|topic)` 按 relevance_rank + year 召回支撑文献。
- **合规**：源表属团队私密资料**不入库**；路径经 `.env` 的 `KB_SOURCE_XLSX` 提供，不写死进开源 `config.yaml`；派生索引缓存落 gitignored 的 `DATA_ROOT/knowledge_base`；源表缺失时优雅降级（空索引 + status，不 500）。
- **二期展望**：PDF 全文到位后按 `kb_file` 关联 → 全文切块/向量化 → 证据检索。
