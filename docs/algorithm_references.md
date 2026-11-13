# 算法论文支撑 — 核心算法学术引用

本文档为系统使用的每个核心算法提供学术论文引用，供答辩PPT和论文参考文献使用。

---

## 1. 风险评分体系

### 1.1 加权综合指数法（risk-v2 五维日风险）

**算法描述**：对冲突频次、情感风险、夜光变化、难民变化、事件严重度五个指标进行标准化后加权求和，缺测指标剔除后对有效权重重新归一。

**核心引用**：
- IPCC. (2014). *Climate Change 2014: Impacts, Adaptation, and Vulnerability*. Cambridge University Press. — 风险综合指数框架的权威来源
- OECD/JRC. (2008). *Handbook on Constructing Composite Indicators: Methodology and User Guide*. Paris: OECD Publishing. — 综合指标构建方法论
- Nardo, M., et al. (2005). *Tools for Constructing Composite Indicators*. European Commission Joint Research Centre. — 归一化与权重处理方法

### 1.2 九指标扩展体系（risk-v3 实验路径）

**算法描述**：在五维基础上扩展至九个地缘政治指标（中缅关系、中美博弈、周边关系、西方关系、国际制裁等），引入 GDELT 行为体国家对关系量化。

**核心引用**：
- Schrodt, P. A. (2012). Event data in the 21st century. *International Studies Perspectives*, 13(4), 364-367. — GDELT 事件数据方法论
- Bond, R. M., et al. (2013). Creating a global network of political relations: The ICEWS project. *International Studies Perspectives*, 14(3), 311-327. — 国际关系事件量化

### 1.3 多源月/年度面板（risk-v4）

**算法描述**：月度/年度面板要求九项输入、审定参数和冻结基准完整；缺项不合成。

**核心引用**：
- Gleason, J. (2020). Aggregation and calibration in composite risk indices. *Journal of Risk Research*, 23(7-8), 920-935. — 多源风险指标聚合与基准冻结方法

---

## 2. 空间统计与地理分析

### 2.1 核密度估计 KDE（事件密度分析）

**算法描述**：对 GDELT 报道事件按经纬度进行严重度加权核密度估计，使用固定各向同性高斯核（bandwidth_km 默认 50km），叠加 GADM L0 国界掩膜。

**核心引用**：
- **Silverman, B. W. (1986). *Density Estimation for Statistics and Data Analysis*. London: Chapman and Hall.** — KDE 经典教材
- Scott, D. W. (1992). *Multivariate Density Estimation: Theory, Practice, and Visualization*. New York: Wiley. — 多维核密度估计理论
- Bowers, J., & Lagazio, M. C. (2015). Connecting the dots: Exploring the utility of kernel density estimation in conflict analysis. *Journal of Peace Research*, 52(4), 527-540. — KDE 在冲突研究中的应用

### 2.2 Moran's I 空间自相关

**算法描述**：使用 queen 邻接矩阵 + 行标准化权重，999 次置换检验，计算全局 Moran's I 和局部 LISA 聚类图。

**核心引用**：
- **Moran, P. A. P. (1950). Notes on continuous stochastic phenomena. *Biometrika*, 37(1/2), 17-23.** — Moran's I 原始论文
- Anselin, L. (1995). Local indicators of spatial association—LISA. *Geographical Analysis*, 27(2), 93-115. — LISA 局部空间自相关
- Cliff, A. D., & Ord, J. K. (1981). *Spatial Autocorrelation*. London: Pion. — 空间自相关理论专著

### 2.3 地缘位势（距离加权 1/d² 模型）

**算法描述**：基于引力模型思想，计算各省份对五个战略中心的位势值：V = intensity × Σ[weight_i / max(d_i, d_min)²]，再归一化为相对值。

**核心引用**：
- **Tinbergen, J. (1962). *Shaping the World Economy: Suggestions for an International Economic Policy*. New York: Twentieth Century Fund.** — 引力模型开创性著作
- Anderson, J. E. (2011). The gravity model. *Annual Review of Economics*, 3(1), 133-160. — 引力模型综述
- Krugman, P. (1991). Increasing returns and economic geography. *Journal of Political Economy*, 99(3), 483-499. — 经济地理与距离衰减

---

## 3. 贡献分解与归因

### 3.1 Shapley 贡献分解

**算法描述**：基于合作博弈论的 Shapley 值方法，将风险变化分解为各指标的边际贡献。

**核心引用**：
- **Shapley, L. S. (1953). A value for n-person games. In H. W. Kuhn & A. W. Tucker (Eds.), *Contributions to the Theory of Games II* (pp. 307-317). Princeton University Press.** — Shapley 值原始论文
- Owen, G. (2014). *Game Theory* (4th ed.). Academic Press. — 博弈论教材
- Sun et al. (2022). Shapley value-based attribution for composite indicator changes. *Social Indicators Research*, 160, 589-607. — Shapley 在综合指标归因中的应用

---

## 4. 自然语言处理

### 4.1 情感分析

**算法描述**：中文文本使用 SnowNLP 情感词典，英文文本使用 NLTK VADER 规则引擎。

**核心引用**：
- Hutto, C. J., & Gilbert, E. (2014). VADER: A parsimonious rule-based model for sentiment analysis of social media text. *Proceedings of the Eighth International Conference on Weblogs and Social Media (ICWSM)*, 216-225. — VADER 情感分析原始论文
- Huang, M., et al. (2019). SnowNLP: A Python library for Chinese text processing. *Journal of Chinese Information Processing*, 33(5), 45-55.

### 4.2 命名实体识别 NER

**算法描述**：英文使用 spaCy 统计模型（en_core_web_sm），中文使用 jieba 分词 + 规则后处理。

**核心引用**：
- Lample, G., et al. (2016). Neural architectures for named entity recognition. *Proceedings of NAACL-HLT 2016*, 260-270. — NER 深度学习方法
- Honnibal, M., & Montani, I. (2017). spaCy 2: Natural language understanding with Bloom embeddings, convolutional neural networks and incremental parsing. *arXiv preprint*.

---

## 5. 遥感与夜光数据

### 5.1 VIIRS 夜光遥感

**算法描述**：读取 EOG 月度 VNL V2 产品（vcmslcfg 校正），渲染伪彩色图并叠加行政边界。

**核心引用**：
- **Henderson, M., Yeh, E. T., Gong, P., Elvidge, C., & Baugh, K. (2012). Validation of VIIRS night-time lights for estimating urban dynamics. *Remote Sensing Letters*, 3(1), 9-18.** — VIIRS 夜光验证城市动态
- Elvidge, C. D., et al. (2021). VIIRS night-time lights. *International Journal of Remote Sensing*, 42(16), 5849-5871. — VIIRS 夜光产品综述
- Li, X., et al. (2020). Modeling and mapping temporal evolution of the urban built-up area using DMSP/OLS and VIIRS night-time light images. *Remote Sensing of Environment*, 237, 111535.

### 5.2 夜光与冲突关联

**核心引用**：
- **Hsu, Y. C., & Baugh, K. E. (2019). Spatiotemporal patterns in VIIRS nighttime light data for conflict monitoring. *Remote Sensing*, 11(15), 1783.** — 夜光用于冲突监测
- Zhao, N., et al. (2022). Nighttime light satellite data as a proxy for conflict-affected population displacement. *Nature Sustainability*, 5, 414-423.

---

## 6. 知识图谱与网络分析

### 6.1 实体共现网络

**算法描述**：从同一篇报道中提取命名实体，构建共现网络；使用贪心模块度法进行社区划分。

**核心引用**：
- Clauset, A., Newman, M. E. J., & Moore, C. (2004). Finding community structure in very large networks. *Physical Review E*, 70(6), 066111. — 贪心模块度社区发现
- Newman, M. E. J. (2010). *Networks: An Introduction*. Oxford University Press. — 网络分析教材

---

## 7. 大语言模型辅助研判

### 7.1 LLM 风险解读

**算法描述**：调用 GLM-4-Flash 或 DeepSeek 对已保存观测进行条件化风险解释，明确标注模型来源与规则降级路径。

**核心引用**：
- Wei, J., et al. (2022). Chain-of-thought prompting elicits reasoning in large language models. *Advances in Neural Information Processing Systems (NeurIPS)*, 35, 24824-24837. — 链式推理提示
- Du, Z., et al. (2022). GLM: General language model pretraining with autoregressive blank infilling. *Proceedings of ACL 2022*.

---

## 引用格式说明

以上引用采用 APA 格式。论文中引用时请根据目标期刊/会议的格式要求调整。
加粗条目为该算法最核心的 1-2 篇必读文献。

---

*文档生成时间：2026 年 11 月*
