"""
utils.config - 配置加载工具
负责读取 config.yaml 并提供全局配置访问
"""
import os
import yaml

_config_cache = None


def load_config(config_path: str = None) -> dict:
    """
    加载 config.yaml 配置文件，返回配置字典。
    支持环境变量 CONFIG_PATH 覆盖默认路径。

    :param config_path: 配置文件路径，默认使用项目根目录下的 config.yaml
    :return: 配置字典
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", None)

    if config_path is None:
        # 默认：项目根目录/config.yaml
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(project_root, "config.yaml")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        _config_cache = yaml.safe_load(f)

    return _config_cache


def get_llm_config() -> dict:
    """获取大模型 API 配置"""
    cfg = load_config()
    return cfg.get("llm", {})


def get_crawler_config() -> dict:
    """获取爬虫配置"""
    cfg = load_config()
    return cfg.get("crawler", {})


def get_storage_config() -> dict:
    """获取数据存储配置"""
    cfg = load_config()
    return cfg.get("storage", {})


def get_risk_weights() -> dict:
    """获取风险评分权重配置"""
    cfg = load_config()
    return cfg.get("risk_weights", {})


def get_trend_config() -> dict:
    """获取趋势分析配置"""
    cfg = load_config()
    return cfg.get("trend", {})


def get_flask_config() -> dict:
    """获取 Flask 服务配置"""
    cfg = load_config()
    return cfg.get("flask", {})


def get_gdelt_config() -> dict:
    """获取 GDELT 配置"""
    cfg = load_config()
    return cfg.get("gdelt", {})


def get_neo4j_config() -> dict:
    """获取 Neo4j 配置"""
    cfg = load_config()
    return cfg.get("neo4j", {})
