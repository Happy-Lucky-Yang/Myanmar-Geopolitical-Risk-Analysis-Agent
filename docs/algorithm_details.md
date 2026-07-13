# 算法实现细节文档

## 1. 风险评分模型

### 1.1 指标定义

| 指标 | 权重 | 范围 | 说明 |
|------|------|------|------|
| conflict_frequency | 0.30 | 0-1 | 冲突关键词/GDELT事件频次 |
| sentiment_avg | 0.25 | 0-1 | 文本情感分析风险分 (负面程度) |
| nightlight_change | 0.20 | 0-1 | 夜间灯光变化率 (VIIRS/WB代理) |
| refugee_change | 0.15 | 0-1 | 难民数量变化率 (WB经济推断) |
| event_severity | 0.10 | 0-1 | 事件严重程度 (GDELT/关键词) |

### 1.2 动态权重归一化

当某指标数据不可用 (值为 0) 时，其权重按比例分配给其他有效指标：

$$w'_i = \frac{w_i}{\sum_{j \in valid} w_j}$$

其中 $valid$ 为有效指标集合 (指标值 > 0)。

### 1.3 综合评分公式

$$RiskScore = 100 \times \sum_{i=1}^{5} w'_i \cdot v_i$$

最终分数四舍五入到 [0, 100] 区间。

### 1.4 风险等级划分

| 分数范围 | 等级 | 颜色 |
|----------|------|------|
| ≥ 80 | 高风险 | 红色 (#f85149) |
| ≥ 60 | 中风险 | 橙色 (#d29922) |
| ≥ 40 | 较低风险 | 黄色 (#e3b341) |
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

### 2.3 关键词增强

若文本包含冲突关键词 (config.yaml 定义)，风险分额外 +0.1~0.3 加成。

---

## 3. 趋势分析

### 3.1 移动平均

窗口大小: 7天 (可配置)

$$MA_t = \frac{1}{w}\sum_{i=t-w+1}^{t} x_i$$

### 3.2 线性回归

使用最小二乘法拟合趋势线:

$$\hat{y} = \beta_0 + \beta_1 x$$

其中:
- $\beta_1 = \frac{n\sum x_i y_i - \sum x_i \sum y_i}{n\sum x_i^2 - (\sum x_i)^2}$
- $\beta_0 = \bar{y} - \beta_1 \bar{x}$

### 3.3 趋势方向判断

| 斜率 $\beta_1$ | 趋势 |
|----------------|------|
| > 0.1 | 上升 |
| < -0.1 | 下降 |
| 其他 | 平稳 |

### 3.4 决定系数 $R^2$

$$R^2 = 1 - \frac{\sum(y_i - \hat{y}_i)^2}{\sum(y_i - \bar{y})^2}$$

$R^2$ 越接近 1，线性趋势越显著。

### 3.5 异常检测 (Z-score)

$$Z = \frac{x_i - \mu}{\sigma}$$

当 $|Z| > 2.0$ 时标记为异常点。

---

## 4. 预测模型

### 4.1 线性回归外推

基于历史数据的线性回归结果，向前外推 7 天:

$$\hat{y}_{t+k} = \beta_0 + \beta_1(t+k)$$

### 4.2 置信度估算

$$confidence = R^2 \times 100\%$$

置信度受数据点数量和 $R^2$ 共同影响。

### 4.3 预测可信区间

基于标准差 $\sigma$ 构建:
- 上界: $\hat{y} + 1.96\sigma$ (95% CI)
- 下界: $\hat{y} - 1.96\sigma$

---

## 5. 夜间灯光遥感指标

### 5.1 数据源

使用 World Bank API 获取缅甸电力指标作为夜光代理:
- `EG.ELC.ACCS.ZS`: 电力覆盖率
- `EG.ELC.LOSS.ZS`: 电力传输损耗率

### 5.2 归一化映射

$$nl\_change = 0.1 + 0.8 \times \frac{access\_pct}{100}$$

传输损耗修正:
$$loss\_factor = \frac{25 - loss\_pct}{20}$$

融合:
$$nl\_change = 0.7 \times nl\_change_{access} + 0.3 \times loss\_factor$$

### 5.3 月度序列展开

年度数据按 12 个月线性插值:
$$v_{month} = v_{year1} + \frac{month-1}{12} \times (v_{year2} - v_{year1})$$

---

## 6. 经济指标归一化

| 指标 | 原始范围 | 映射公式 | 含义 |
|------|----------|---------|------|
| GDP增长率 | [-10%, 15%] | (raw+10)/25 | 经济增长 |
| 通胀率 | [-5%, 30%] | (30-raw)/35 | 高通胀=负面 |
| 贸易占GDP | [0%, 100%] | raw/100 | 经济开放度 |

### 6.1 难民变化估算

基于经济指标推断:
$$refugee\_change = 0.6 + 0.4 \times economic\_distress$$

其中:
- $GDP < 0$: distress += 0.6 × min(1, |GDP|/10)
- $GDP < 3%$: distress += 0.3
- $inflation > 15%$: distress += 0.4 × min(1, (infl-15)/20)

---

## 7. NetworkX 网络分析

### 7.1 中心性指标

- **度中心性**: $C_D(v) = \frac{deg(v)}{n-1}$
- **介数中心性**: $C_B(v) = \sum_{s \neq v \neq t} \frac{\sigma_{st}(v)}{\sigma_{st}}$
- **接近中心性**: $C_C(v) = \frac{n-1}{\sum_{t} d(v,t)}$

### 7.2 社区检测

使用 Louvain 算法 (`networkx.community.louvain_communities`):
- 优化模块度 Q
- 降级方案: 连通分量

---

## 8. 多模态时空对齐

### 8.1 Pearson 相关系数

$$r = \frac{\sum(x_i - \bar{x})(y_i - \bar{y})}{\sqrt{\sum(x_i - \bar{x})^2 \cdot \sum(y_i - \bar{y})^2}}$$

用于衡量夜光变化与冲突频次的相关性。

---

## 9. 链式推理

### 9.1 推理链模板

1. **事件识别** → 分类事件类型、行为体、地点
2. **影响分析** → 基于事件信息评估中缅关系影响
3. **趋势研判** → 短期/中期趋势、升级概率
4. **建议生成** → 政策建议、风险缓解措施

### 9.2 置信度估算

$$confidence = \frac{actual\_fields}{expected\_fields} \times 0.8 + 0.2$$
