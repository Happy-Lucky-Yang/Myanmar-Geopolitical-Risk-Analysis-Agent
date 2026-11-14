"""VIIRS 夜光渲染：数据版本选择、观测掩膜、固定色标与接口契约（合成 GeoTIFF，无私密数据依赖）。"""
import os

import numpy as np
import pytest

rasterio = pytest.importorskip('rasterio')
from rasterio.transform import from_origin  # noqa: E402

from visualization import viirs_renderer as vr  # noqa: E402

# 与真实交付同一外包框：92–101.5E，9.5–28.6N
_W, _H = 38, 76
_TRANSFORM = from_origin(92.0, 28.6, 9.5 / _W, 19.1 / _H)


def _pixel(lon, lat):
    col = int((lon - 92.0) / (9.5 / _W))
    row = int((28.6 - lat) / (19.1 / _H))
    return row, col


def _write(path, rad, cvg, nodata=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with rasterio.open(path, 'w', driver='GTiff', width=_W, height=_H, count=2, dtype='float32',
                       crs='EPSG:4326', transform=_TRANSFORM, nodata=nodata) as dst:
        dst.write(rad.astype('float32'), 1)
        dst.write(cvg.astype('float32'), 2)


def _vector_arrays():
    rad = np.full((_H, _W), 0.2, dtype='float32')
    cvg = np.full((_H, _W), 5, dtype='float32')
    rad[:, :4] = -9999          # 国界外 nodata
    cvg[:, :4] = -9999
    rad[_pixel(96.15, 16.8)] = 45.0   # 仰光
    cvg[60:, 10:] = 0            # 南部无有效观测
    rad[60:, 10:] = 0
    return rad, cvg


@pytest.fixture
def viirs_root(tmp_path, monkeypatch):
    root = tmp_path / 'nightlight'
    vec = root / 'MMR_VNL_矢量裁剪版' / 'MMR_VNL_202001-202003' / 'data_mmr'
    rule = root / 'MMR_VNL_规则裁剪版_2020-2023' / 'MMR_VNL_202001-202003' / 'data'
    rad, cvg = _vector_arrays()
    _write(str(vec / 'MMR_VNL_202001.tif'), rad, cvg, nodata=-9999)
    rule_rad = np.full((_H, _W), 0.2, dtype='float32')
    rule_rad[_pixel(100.5, 13.75)] = 900.0   # 曼谷：规则版外包框内但不在缅甸境内
    rule_rad[_pixel(96.15, 16.8)] = 30.0
    _write(str(rule / 'MMR_VNL_202001.tif'), rule_rad, np.full((_H, _W), 4, dtype='float32'))
    _write(str(rule / 'MMR_VNL_202003.tif'), rule_rad, np.full((_H, _W), 4, dtype='float32'))
    monkeypatch.setenv('VIIRS_ROOT', str(root))
    vr.reset_cache()
    yield root
    vr.reset_cache()


def test_vector_version_preferred_and_missing_months(viirs_root):
    assert vr.list_available_months() == ['2020-01', '2020-03']
    assert vr.missing_months() == ['2020-02']
    assert 'data_mmr' in vr.get_file_path('2020-01')
    status = vr.status()
    assert status['healthy'] and status['sources'] == {'vector': 1, 'rule': 1}


def test_no_observation_pixels_excluded_from_stats(viirs_root):
    meta = vr.render_meta('2020-01')
    assert meta['source'] == 'vector'
    land = (_W - 4) * _H
    no_obs = (_H - 60) * (_W - 10)
    assert meta['land_pixels'] == land
    assert meta['coverage_pct'] == round(100 * (land - no_obs) / land, 1)
    assert meta['max_rad'] == 45.0


def test_rule_version_masked_to_myanmar(viirs_root):
    meta = vr.render_meta('2020-03')
    assert meta['source'] == 'rule'
    # 曼谷 900 nW 必须被国界掩膜剔除，最大值来自仰光
    assert meta['max_rad'] == 30.0
    assert meta['land_pixels'] < _W * _H


@pytest.mark.parametrize('month', ['2020-1', '202013', 'abc', '../x'])
def test_normalize_month_rejects_invalid(month):
    assert vr.normalize_month(month) is None


def test_render_outputs_png_and_caches(viirs_root):
    first = vr.render_viirs('2020-01', dpi=60)
    assert first[:8] == b'\x89PNG\r\n\x1a\n'
    assert vr.render_viirs('202001', dpi=60) is first
    assert vr.render_thumbnail('2020-01')[:4] == b'\x89PNG'
    with pytest.raises(ValueError):
        vr.render_viirs('2020-02')


def test_viirs_api_contract(viirs_root):
    from app import app
    client = app.test_client()
    thumb = client.get('/api/viirs/image?month=2020-01&format=png&size=thumb')
    assert thumb.status_code == 200 and thumb.mimetype == 'image/png'
    web = client.get('/api/viirs/image?month=2020-01&format=web&dpi=60').get_json()
    assert web['success'] and web['data']['image'].startswith('data:image/png;base64,')
    assert web['data']['meta']['source'] == 'vector'
    hd = client.get('/api/viirs/image?month=2020-01&format=hd&dpi=150')
    assert hd.mimetype == 'image/png' and 'attachment' in hd.headers['Content-Disposition']
    assert client.get('/api/viirs/image?month=2020-13').status_code == 400
    assert client.get('/api/viirs/image?month=2020-02').status_code == 404
    assert client.get('/api/viirs/image?month=2020-01&format=svg').status_code == 400
    assert client.get('/api/viirs/status').get_json()['data']['missing'] == ['2020-02']
    page = client.get('/remote-sensing').get_data(as_text=True)
    assert '缺 2020-02' in page and 'class="rs-alert"' not in page
    assert 'format=png&size=thumb' in page


def test_empty_root_reports_unhealthy(tmp_path, monkeypatch):
    monkeypatch.setenv('VIIRS_ROOT', str(tmp_path / 'missing'))
    vr.reset_cache()
    try:
        from app import app
        client = app.test_client()
        assert client.get('/api/viirs/status').status_code == 503
        page = client.get('/remote-sensing').get_data(as_text=True)
        assert 'class="rs-alert"' in page and 'VIIRS_ROOT' in page
        assert client.get('/api/viirs/image').status_code == 404
    finally:
        vr.reset_cache()
