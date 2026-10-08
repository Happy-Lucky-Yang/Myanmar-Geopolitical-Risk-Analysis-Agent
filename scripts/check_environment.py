"""Python 3.12 核心环境与可选NLP能力的离线检查。"""
import importlib.util
import importlib
import sys


REQUIRED_MODULES = (
    "flask", "flask_cors", "requests", "bs4", "pandas", "numpy",
    "jieba", "snownlp", "nltk", "folium", "pyecharts", "networkx",
    "wbgapi", "docx", "jinja2", "yaml", "spacy",
    "pytest", "sqlalchemy", "psycopg", "alembic", "geoalchemy2", "shapely", "pyproj",
)
OPTIONAL_MODULES = ("openai", "neo4j", "LAC", "paddle")


def _resource_available(resource_find, candidates):
    for candidate in candidates:
        try:
            resource_find(candidate)
            return True
        except (LookupError, OSError):
            continue
    return False


def check_dependencies(find_spec=importlib.util.find_spec, resource_find=None,
                       importer=None):
    """Return import and NLP-resource availability without network access.

    The CLI supplies ``importer`` and therefore performs real imports/model loading;
    tests may omit it to perform a lightweight deterministic discovery check.
    """
    required = {}
    for name in REQUIRED_MODULES:
        available = find_spec(name) is not None
        if available and importer is not None:
            try:
                importer(name)
            except Exception:
                available = False
        required[name] = available

    optional = {}
    for name in OPTIONAL_MODULES:
        available = find_spec(name) is not None
        if available and importer is not None:
            try:
                importer(name)
            except Exception:
                available = False
        optional[name] = available

    model_available = find_spec("en_core_web_sm") is not None
    if model_available and importer is not None and required.get("spacy"):
        try:
            importer("spacy").load("en_core_web_sm")
        except Exception:
            model_available = False

    if resource_find is None and required.get("nltk"):
        try:
            resource_find = importer("nltk").data.find if importer else None
        except Exception:
            resource_find = None
    vader_available = bool(resource_find) and _resource_available(resource_find, (
        "sentiment/vader_lexicon.zip",
        "sentiment/vader_lexicon/vader_lexicon.txt",
    ))

    return {
        "required": required,
        "optional": optional,
        "resources": {
            "en_core_web_sm": model_available,
            "vader_lexicon": vader_available,
        },
    }


def main(version_info=None, find_spec=importlib.util.find_spec,
         resource_find=None, importer=importlib.import_module) -> int:
    version_info = tuple(version_info or sys.version_info[:2])
    version_ok = version_info[:2] == (3, 12)
    if not version_ok:
        print(f"[FAIL] 当前 Python {version_info[0]}.{version_info[1]}，验收版本为 Python 3.12")

    result = check_dependencies(
        find_spec=find_spec,
        resource_find=resource_find,
        importer=importer,
    )
    for group in ("required", "optional"):
        label = "必需" if group == "required" else "可选"
        for name, available in result[group].items():
            print(f"[{label}] {name}: {'OK' if available else 'MISSING'}")

    for name, available in result["resources"].items():
        print(f"[资源] {name}: {'OK' if available else 'MISSING'}")

    missing = [name for name, ok in result["required"].items() if not ok]
    missing_resources = [name for name, ok in result["resources"].items() if not ok]
    if missing or missing_resources or not version_ok:
        if missing:
            print("缺少必需依赖: " + ", ".join(missing))
        if missing_resources:
            print("缺少 NLP 资源: " + ", ".join(missing_resources))
        return 1
    print("依赖导入检查通过；可选依赖缺失时对应能力将显式降级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
