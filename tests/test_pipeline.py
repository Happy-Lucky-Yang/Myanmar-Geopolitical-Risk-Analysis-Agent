"""Offline end-to-end tests for the unified pipeline."""
import json
from pathlib import Path

import pytest


def _reset_data_paths():
    from utils.config import reset_data_paths
    reset_data_paths()


def test_demo_pipeline_is_offline_and_writes_all_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _reset_data_paths()

    import requests
    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network access")),
    )
    monkeypatch.setattr(
        "analyzer.report_generator.get_report_generator",
        lambda: (_ for _ in ()).throw(AssertionError("report generator may fetch World Bank data")),
    )

    from pipeline import run_pipeline
    result = run_pipeline(source_mode="demo", include_llm=True, persist=True)

    assert result["success"] is True
    assert result["stages"]["source"]["status"] == "ok"
    assert result["stages"]["llm"]["status"] == "skipped"
    assert result["stages"]["report"]["status"] == "ok"
    assert result["processed_count"] == 5
    for name in ("summary", "map", "trend", "report"):
        assert Path(result["artifacts"][name]).is_file(), name

    saved_summary = json.loads(
        Path(result["artifacts"]["summary"]).read_text(encoding="utf-8")
    )
    assert saved_summary["success"] is True
    assert saved_summary["stages"]["artifacts"]["status"] == "ok"
    assert saved_summary["artifacts"] == result["artifacts"]


def test_demo_pipeline_result_uses_stable_stage_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _reset_data_paths()

    from pipeline import run_pipeline
    result = run_pipeline(source_mode="demo", persist=False)

    assert set(result) == {
        "success", "source_mode", "processed_count", "stages", "warnings",
        "risk", "trend", "artifacts",
    }
    assert all(stage["status"] in {"ok", "skipped", "degraded", "failed"}
               for stage in result["stages"].values())


def test_pipeline_rejects_unknown_source_mode():
    from pipeline import run_pipeline

    with pytest.raises(ValueError, match="source_mode"):
        run_pipeline(source_mode="unknown", persist=False)


def test_pipeline_returns_failed_stage_when_history_persistence_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _reset_data_paths()

    from analyzer.data_loader import get_data_loader
    loader = get_data_loader()

    def fail_persist(**kwargs):
        raise OSError("disk is read-only")

    monkeypatch.setattr(loader, "append_risk_score", fail_persist)
    from pipeline import run_pipeline
    result = run_pipeline(source_mode="demo", persist=True)

    assert result["success"] is False
    assert result["stages"]["persist"]["status"] == "failed"
    assert any("disk is read-only" in warning for warning in result["warnings"])


def test_pipeline_returns_failed_stage_when_artifact_write_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path))
    _reset_data_paths()

    import pipeline

    def fail_artifacts(result, run_dir):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "_write_artifacts", fail_artifacts)
    result = pipeline.run_pipeline(source_mode="demo", persist=True)

    assert result["success"] is False
    assert result["stages"]["artifacts"]["status"] == "failed"
    assert any("disk full" in warning for warning in result["warnings"])


def test_pipeline_degrades_when_optional_llm_client_cannot_initialize(monkeypatch):
    import copy
    import pipeline

    monkeypatch.setattr(
        pipeline,
        "_load_source",
        lambda source_mode, result: copy.deepcopy(pipeline.DEMO_NEWS),
    )
    monkeypatch.setattr(
        "analyzer.llm_client.get_llm_client",
        lambda: (_ for _ in ()).throw(RuntimeError("LLM configuration invalid")),
    )

    result = pipeline.run_pipeline(
        source_mode="existing", include_llm=True, persist=False
    )

    assert result["success"] is True
    assert result["stages"]["llm"]["status"] == "degraded"
    assert any("LLM configuration invalid" in warning for warning in result["warnings"])


def test_pipeline_returns_storage_failure_when_loader_cannot_initialize(monkeypatch):
    import pipeline

    monkeypatch.setattr(
        "analyzer.data_loader.get_data_loader",
        lambda: (_ for _ in ()).throw(PermissionError("DATA_ROOT is not writable")),
    )

    result = pipeline.run_pipeline(source_mode="demo", persist=True)

    assert result["success"] is False
    assert result["stages"]["storage"]["status"] == "failed"
    assert any("DATA_ROOT is not writable" in warning for warning in result["warnings"])
