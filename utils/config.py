"""
utils.config - 配置加载工具
负责读取 config.yaml 并提供全局配置访问

密钥安全方案（防止真实密钥随 config.yaml 提交到开源仓库）：
  1. 启动时自动加载 .env 文件到环境变量（无需 python-dotenv 依赖）
     查找顺序: 环境变量 ENV_FILE 指定路径 > 项目根目录 .env
  2. 敏感字段（LLM/Neo4j 密钥）优先从环境变量读取，覆盖 yaml 占位符
  3. 含 "your-" 前缀的占位符值不参与覆盖（避免模板值覆盖真实配置）

推荐用法: 真实密钥只写入 .env（已被 .gitignore 拦截），config.yaml 永远保持占位符。
若 .env 存放在项目外（如私密文件夹），设置系统环境变量 ENV_FILE 指向它即可。
"""
import os
import yaml

_config_cache = None
_env_loaded = False

# 环境变量 → config 字段的覆盖映射（仅敏感字段）
_ENV_OVERRIDES = [
    ("LLM_API_URL", "llm", "base_url"),
    ("LLM_API_KEY", "llm", "api_key"),
    ("LLM_MODEL_NAME", "llm", "model_name"),
    ("NEO4J_URI", "neo4j", "uri"),
    ("NEO4J_USER", "neo4j", "user"),
    ("NEO4J_PASSWORD", "neo4j", "password"),
]


def _load_env_file():
    """
    轻量 .env 解析器（KEY=VALUE 每行一条，# 开头为注释）
    已存在的系统环境变量不会被覆盖（setdefault 语义）
    """
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.environ.get("ENV_FILE", ""),          # 优先: 显式指定（可指向项目外私密目录）
        os.path.join(project_root, ".env"),       # 其次: 项目根目录
    ]

    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value:
                        os.environ.setdefault(key, value)
        except Exception:
            pass  # .env 解析失败不应阻断启动


def _apply_env_overrides(cfg: dict):
    """用环境变量覆盖敏感配置字段（占位符值跳过）"""
    for env_key, section, field in _ENV_OVERRIDES:
        value = os.environ.get(env_key, "").strip()
        # 跳过空值与模板占位符（如 your-api-key-here / http://your-lab-server...）
        if not value or "your-" in value:
            continue
        cfg.setdefault(section, {})[field] = value


def load_config(config_path: str = None) -> dict:
    """
    加载 config.yaml 配置文件，返回配置字典。
    支持环境变量 CONFIG_PATH 覆盖默认路径；
    敏感字段（密钥）自动从 .env / 环境变量覆盖，详见模块注释。

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
        cfg = yaml.safe_load(f)

    # 密钥安全: .env 加载 + 环境变量覆盖
    _load_env_file()
    _apply_env_overrides(cfg)

    _config_cache = cfg
    return _config_cache


def reset_config():
    """重置配置缓存（用于热重载或测试）"""
    global _config_cache
    _config_cache = None


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
