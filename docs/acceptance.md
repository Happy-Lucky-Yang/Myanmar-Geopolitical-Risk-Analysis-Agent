# 核心功能验收说明

## 自动化基线

```bash
python -m pytest -q
python run_full_pipeline.py --demo
python scripts/check_environment.py
```

离线演示应返回成功状态，并在 `DATA_ROOT/processed/runs/<timestamp>/` 生成
`summary.json`、`map.html`、`trend.json` 和 `report.html`。可选组件缺失时，
产物仍会生成，但阶段状态为 `degraded` 并给出原因。

## NER 人工标注验收

历史组需提供至少 50 条真实新闻或历史事件，JSON 格式如下：

```json
[
  {
    "text": "待标注新闻正文",
    "entities": {
      "locations": ["掸邦"],
      "organizations": ["联合国"],
      "persons": [],
      "events": ["冲突"]
    }
  }
]
```

执行 `python scripts/evaluate_acceptance.py --ner-gold 标注文件.json`。
验收指标使用带实体类型的 micro-F1，数据不少于 50 条且 micro-F1 ≥ 0.70
才判定通过。仓库现有 48 条历史事件不是完整 NER 金标准，不能自动冒充人工标注。

## 情感与 LLM 验收

- `tests/fixtures/sentiment_gold.json` 提供 30 条中英文正面、中性、负面基线，
  风险等级一致率要求 ≥ 70%。
- LLM 自动测试使用模拟响应；最终需配置真实 Key，抽取一条新闻确认所有结构字段完整，
  并由历史/地科同学对 100 条输出标记“正确/部分正确/错误”。

## 协作模块复核

- 爬虫验收当天将 `config.yaml` 的 `storage.format` 设为 `csv`，确认 CSV 中
  至少 20 条有效新闻且字段齐全；默认 JSON 仅用于日常缓存。
- 清洗结果中标题与正文不得同时为空，日期统一为 `YYYY-MM-DD`。
- 启用 Neo4j 后运行种子脚本，浏览器中确认至少 30 个节点。
- 打开地图、趋势页和生成报告，确认可以交互查看且无未说明的合成数据。
