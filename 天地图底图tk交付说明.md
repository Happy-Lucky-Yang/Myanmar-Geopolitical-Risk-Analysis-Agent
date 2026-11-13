# 天地图底图 tk 交付说明

## 一、这是什么

这是天地图“浏览器端”应用的 tk，用于把现有 Folium 地图底图从 CartoDB dark_matter 替换为天地图在线 WMTS 底图。

它只授权调用天地图地图服务，不是账号密码，不能登录天地图控制台，也不能修改应用配置。

## 二、交付内容

| 项目 | 内容 |
|---|---|
| tk | dbb262cf04e5dd5c90d243352df13670  |
| 应用类型 | 浏览器端 |
| 底图类型 | 天地图矢量底图 vec + 矢量注记 cva（EPSG:3857） |
| 域名白名单 | localhost, 127.0.0.1,【部署域名】 |

如果实际访问域名不在白名单，瓦片会返回 401/403，请先告知我加白名单。

## 三、代码修改位置（当前仓库）

- 文件：`visualization/map_gen.py`
- 函数：`_add_basemap(m)`（约 123–169 行）

仓库里的 `generate_heatmap`、`generate_risk_hybrid_map`、`generate_unified_map`、`generate_default_map`、`generate_event_density_map`、`generate_notice_map` 都调用这个函数，所以只改 `_add_basemap` 一处即可，不要逐个改 `generate_*`。

现有 `folium.Map(..., tiles=None)` 已经就绪，不用动。

## 四、修改方式

1. 文件顶部加 `import os`。
2. 新增 `_tianditu_url(layer, tk)` 用于拼接瓦片地址。
3. 在 `_add_basemap` 里读取 `os.environ.get("TDT_TK", "").strip()`：
   - 有 tk：主底图用 `vec_w`，注记用 `cva_w`；
   - 无 tk：保留原 CartoDB dark_matter 作为回退，避免地图空白；
   - 原来的 Esri 暗灰兜底通道和底部状态提示请保留，状态脚本仍绑定 `primary` 和 `fallback` 两个变量。
4. 瓦片地址模板：

```text
https://t0.tianditu.gov.cn/vec_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=vec&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=【tk】
```

注记层把 `vec` 换成 `cva`，`LAYER=cva`。

注意 `TILEROW={y}`、`TILECOL={x}`，不要写反；`max_zoom` 建议 18。

## 五、tk 配置

本地运行：

- 项目根目录 `.env` 加一行 `TDT_TK=dbb262cf04e5dd5c90d243352df13670`
- `utils/config.py` 启动时会载入

Docker 运行：

- `.env` 不会自动进容器（Dockerfile 设置了 `ENV_FILE=/nonexistent`，`.dockerignore` 也排除了 `.env`）
- 必须在 `compose.yaml` 的 web/worker 共用 environment 里显式加：

```yaml
TDT_TK: ${TDT_TK}
```

不要把 tk 提交到 Git，也不要写进 `.env.example`。

## 六、验收标准

1. 浏览器打开以下地址能返回 XML：

```text
https://t0.tianditu.gov.cn/vec_w/wmts?request=GetCapabilities&service=wmts&tk=【tk】
```

2. 打开 `/map` 页面，F12 Network 中瓦片请求返回 200；
3. 缩放、拖拽无空白板块，注记正常显示；
4. 控制台没有 401/403。

## 七、注意事项

1. tk 会出现在生成的 HTML 源码里，这是浏览器端 key 的正常现象；防护靠域名白名单和配额，不要转发到公开群。
2. 页面必须用 HTTPS，瓦片地址也必须用 https，否则会被混合内容拦截。
3. 更换域名或端口前，先同步天地图控制台的白名单。
4. 天地图全球高分辨率覆盖有限：全球影像约 1–10 级、中国周边 8–12 级、重点区域 11–16 级；境外高缩放级别可能没有瓦片，先按现有 zoom 6 测试，不要假设全球任意区域都能到 18 级。
5. 天地图矢量底图偏亮，现有系统是暗色主题；先按 vec/cva 接入，验收后如需要更贴合，可改用 img/cia 影像底图，或调整透明度/样式。
6. 个人 tk 约 1 万次/天/服务配额，瓦片请求也计数；公开部署或高并发需要单位开发者。
7. tk 只支持在线调用，不要用下载器批量抓取瓦片；如果需要内网或离线部署，需另行申请授权，这个 tk 不覆盖离线场景。
8. 地图上保留“© 天地图”署名。

## 八、待确认

1. 最终部署域名（用于加白名单）；
2. 用矢量底图还是影像底图；
3. 运行环境是否能访问外网；
4. 是否用 Docker 部署（决定 tk 的注入方式）。
