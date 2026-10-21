"""新增产品能力显式开关；不改变部署或现有数据源配置。"""
import os


def enabled(name):
    # .env 仅在首次 load_config() 时加载；开关可能先于它被查询，这里主动确保已加载
    from utils.config import _load_env_file
    _load_env_file()
    return os.environ.get('ENABLE_' + name.upper(), 'false').lower() == 'true'


def flags():
    return {name: enabled(name) for name in ('literature', 'workspace', 'risk_briefing', 'auto_briefing', 'un_news',
                                                           'risk_panels', 'acled', 'ofac', 'idmc', 'viirs', 'evidence_weighting')}
