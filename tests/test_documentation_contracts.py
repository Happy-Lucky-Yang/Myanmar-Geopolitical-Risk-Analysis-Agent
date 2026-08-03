"""Keep public documentation aligned with actual routes and algorithms."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_does_not_claim_twenty_api_endpoints():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "20 个 API" not in readme
    assert "API 接口一览（20 个）" not in readme


def test_api_documentation_includes_current_page_and_key_routes():
    api_docs = (ROOT / "docs/api_examples.md").read_text(encoding="utf-8")
    assert "`/dashboard`" in api_docs
    assert "`/api/chain`" in api_docs
    assert "`/api/report`" in api_docs
    assert '"warnings"' in api_docs

    analyze_section = api_docs.split("## 2. 文本分析接口", 1)[1].split("---", 1)[0]
    assert '"warnings"' in analyze_section


def test_algorithm_documentation_matches_implemented_thresholds_and_sentiment_flow():
    details = (ROOT / "docs/algorithm_details.md").read_text(encoding="utf-8")
    assert "0.005" in details
    assert "冲突关键词计入 conflict_frequency 和 event_severity" in details
    assert "风险分额外 +0.1~0.3" not in details


def test_acceptance_document_explains_external_ner_labels_and_csv_review():
    acceptance = (ROOT / "docs/acceptance.md").read_text(encoding="utf-8")
    assert "至少 50 条" in acceptance
    assert "micro-F1" in acceptance
    assert "storage.format" in acceptance
    assert "至少 20 条" in acceptance


def test_readme_does_not_describe_the_suite_as_crawler_only():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "当前仅爬虫模块" not in readme


def test_readme_names_both_ner_runtimes_and_optional_services():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "LAC + spaCy" in readme
    assert "LLM 和 Neo4j 均为运行时可选能力" in readme
