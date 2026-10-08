"""Environment and reproducibility contract tests."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_requirements_include_english_ner_runtime():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "spacy==" in requirements
    assert "pytest==" in requirements


def test_llm_runtime_controls_are_configured():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    llm = config["llm"]
    assert llm["max_retries"] >= 1
    assert llm["timeout_seconds"] >= 1
    assert isinstance(llm["cache_enabled"], bool)


def test_readme_documents_python_and_nlp_resources():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Python 3.12" in readme
    assert "python -m spacy download en_core_web_sm" in readme
    assert 'nltk.download("vader_lexicon")' in readme


def test_environment_checker_distinguishes_required_and_optional_dependencies():
    from scripts.check_environment import check_dependencies

    available = {"flask", "yaml"}
    result = check_dependencies(find_spec=lambda name: object() if name in available else None)

    assert result["required"]["flask"] is True
    assert result["required"]["snownlp"] is False
    assert result["required"]["pytest"] is False
    assert result["optional"]["neo4j"] is False
    assert result["optional"]["openai"] is False
    assert result["optional"]["LAC"] is False
    assert 'LAC' not in result['required']
    assert 'jieba' in result['required']


def test_environment_checker_requires_python_312_and_nlp_resources():
    from scripts.check_environment import check_dependencies, main

    always_installed = lambda name: object()

    def missing_resource(name):
        raise LookupError(name)

    result = check_dependencies(
        find_spec=always_installed,
        resource_find=missing_resource,
    )

    assert result["resources"]["en_core_web_sm"] is True
    assert result["resources"]["vader_lexicon"] is False
    assert main(
        version_info=(3, 11),
        find_spec=always_installed,
        resource_find=lambda name: object(),
        importer=lambda name: type("Module", (), {"load": lambda self, model: object()})(),
    ) == 1


def test_default_web_configuration_is_local_and_non_debug():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    flask = config["flask"]

    assert flask["host"] in {"127.0.0.1", "localhost"}
    assert flask["debug"] is False
    assert flask["cors_origins"]
