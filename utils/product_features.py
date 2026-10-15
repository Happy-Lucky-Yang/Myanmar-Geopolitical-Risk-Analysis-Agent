"""新增产品能力显式开关；不改变部署或现有数据源配置。"""
import os


def enabled(name):
    return os.environ.get('ENABLE_' + name.upper(), 'false').lower() == 'true'


def flags():
    return {name: enabled(name) for name in ('literature', 'workspace', 'risk_briefing', 'auto_briefing', 'un_news')}
