# -*- coding: utf-8 -*-
"""底图通道选择单元测试：有天地图 tk 时用 vec/cva，无 tk 时保持 Esri/OSM（无网络依赖）"""
import folium

from visualization.map_gen import _add_basemap, _tianditu_url


def _tile_layers(m):
    return [c for c in m._children.values() if isinstance(c, folium.TileLayer)]


def test_tianditu_url_row_col_not_swapped():
    url = _tianditu_url("vec", "abc")
    assert url.startswith("https://t{s}.tianditu.gov.cn/vec_w/wmts?")
    assert "LAYER=vec" in url and "TILEMATRIXSET=w" in url
    assert "TILEMATRIX={z}&TILEROW={y}&TILECOL={x}" in url
    assert url.endswith("&tk=abc")
    assert "cva_w/wmts?" in _tianditu_url("cva", "abc") and "LAYER=cva" in _tianditu_url("cva", "abc")


def test_tianditu_basemap_layers(monkeypatch):
    monkeypatch.setenv("TDT_TK", "fixture-tk")
    m = folium.Map(location=[19, 96], zoom_start=6, tiles=None)
    _add_basemap(m)
    urls = [layer.tiles for layer in _tile_layers(m)]
    assert any("cva_w" in u for u in urls) and any("vec_w" in u for u in urls)
    assert any("arcgisonline" in u for u in urls)          # Esri 兜底
    assert not any("openstreetmap" in u for u in urls)
    html = m.get_root().render()
    assert '"subdomains": "01234567"' in html             # 否则 {s} 会落成 a/b/c
    assert "天地图" in html


def test_no_tk_keeps_esri_osm(monkeypatch):
    monkeypatch.delenv("TDT_TK", raising=False)
    m = folium.Map(location=[19, 96], zoom_start=6, tiles=None)
    _add_basemap(m)
    urls = [layer.tiles for layer in _tile_layers(m)]
    assert not any("tianditu" in u for u in urls)
    assert any("arcgisonline" in u for u in urls) and any("openstreetmap" in u for u in urls)
