"""测试只能使用临时数据目录；默认禁止网络，避免误写真实运行数据。"""
import os
import socket
import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'postgis: 需要显式确认的独立PostGIS测试库，不能用SQLite替代')
    config.addinivalue_line('markers', 'browser: 离线浏览器，允许事件循环内部回环通信，页面请求均拦截')


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch, request):
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LLM_API_KEY", "your-test-key")
    monkeypatch.setenv("STORAGE_BACKEND", "file")
    monkeypatch.setenv("APP_SHARED_MODE", "false")
    from utils.config import reset_config, reset_data_paths
    reset_config()
    reset_data_paths()
    # 单例不能跨用例持有上一用例的目录、缓存和数据库连接。
    instances = {
        "analyzer.data_loader": "_loader_instance",
        "analyzer.llm_client": "_llm_instance",
        "analyzer.alert_monitor": "_instance",
        "data.event_store": "_instance",
        "data.source_health": "_instance",
    }
    for module_name, attribute in instances.items():
        module = sys.modules.get(module_name)
        if module and hasattr(module, attribute):
            monkeypatch.setattr(module, attribute, None)
    if request.node.get_closest_marker("postgis") is None:
        original_connect = socket.socket.connect
        def deny_network(sock, address):
            if (request.node.get_closest_marker('browser') is not None
                    and isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}):
                return original_connect(sock, address)
            raise OSError("离线测试禁止网络访问，请使用模拟响应")
        monkeypatch.setattr(socket.socket, "connect", deny_network)
    yield
    reset_config()
    reset_data_paths()
