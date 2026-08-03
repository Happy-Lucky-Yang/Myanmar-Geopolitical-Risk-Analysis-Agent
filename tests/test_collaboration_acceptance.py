"""Executable checks for the modules assigned for technical review."""
import csv
from pathlib import Path
from unittest.mock import patch


def test_preprocess_removes_blank_rows_and_normalizes_dates(tmp_path):
    from analyzer.data_loader import DataLoader

    loader = DataLoader(str(tmp_path / "raw"), str(tmp_path / "processed"), str(tmp_path / "external"))
    result = loader.preprocess([
        {"title": "<b>有效新闻</b>", "content": " 正文\t内容 ", "pub_time": "2026/06/15"},
        {"title": "", "content": "", "pub_time": "2026-06-15"},
    ])

    assert len(result) == 1
    assert result[0]["title"] == "有效新闻"
    assert result[0]["content"] == "正文 内容"
    assert result[0]["pub_time"] == "2026-06-15"


def test_crawler_can_export_twenty_rows_as_csv(tmp_path, monkeypatch):
    from utils.config import reset_data_paths
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    reset_data_paths()

    with patch("data.crawler.get_crawler_config", return_value={"sources": []}), \
         patch("data.crawler.get_storage_config", return_value={"format": "csv"}):
        from data.crawler import NewsCrawler
        crawler = NewsCrawler()

    rows = [{
        "title": f"新闻 {index}",
        "pub_time": "2026-06-15",
        "content": f"正文 {index}",
        "url": f"https://example.invalid/{index}",
        "source": "验收源",
        "crawled_at": "2026-06-15T00:00:00",
    } for index in range(20)]
    output = Path(crawler.save_news(rows))

    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        saved = list(csv.DictReader(handle))
    assert output.suffix == ".csv"
    assert len(saved) == 20
