"""出站请求通道：配置代理与直连互为兜底。

本机代理（Clash 等）可能未启动，也可能在监听但出口不通；反过来部分源只认直连。
因此对每次请求按“配置通道优先、另一通道兜底”的顺序尝试，任一通道成功即算采到数据。
get_proxy() 带端口探测缓存（60s），长时间运行的单例不会每请求都建连探测。
"""
import logging

logger = logging.getLogger(__name__)
_fallback_logged = set()


def proxy_channels() -> list:
    """按优先级返回通道列表（requests 的 proxies 字典；None 表示直连）。"""
    from utils.config import get_proxy
    proxy = get_proxy()
    return [proxy, None] if proxy else [None]


def channel_for(attempt: int, channels: list):
    """第 attempt 次尝试使用的通道；尝试次数超出通道数后沿用最后一条。"""
    return channels[min(attempt, len(channels) - 1)]


def note_fallback(host: str):
    """同一主机只记一次通道切换告警，避免重试时刷屏。"""
    if host not in _fallback_logged:
        _fallback_logged.add(host)
        logger.warning(f"[Net] {host} 首选通道失败，已切换到备用通道")
