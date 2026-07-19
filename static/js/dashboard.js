/**
 * 综合态势仪表盘 JS
 * 依赖: common.js + ECharts CDN
 * 整合: 预警 / 地缘位势 / 空间自相关 / 关系网络 / 诊断归因 / 多源融合 / 历史时间线
 */

var mmChart = null;

document.addEventListener('DOMContentLoaded', function () {
    loadAll();
});

function loadAll() {
    loadAlert();
    loadGeoPotential();
    loadNetwork();
    loadDiagnostic();
    loadMultimodal();
    loadHistory();
}

/* ================= 预警面板 ================= */
async function loadAlert() {
    var panel = document.getElementById('alert-panel');
    var indicator = document.getElementById('alert-indicator');
    renderLoading(panel);
    try {
        var json = await fetchJSON('/api/alert');
        hideLoading(panel);
        if (!json.success) { panel.innerHTML = errBox(json.error); return; }
        var s = json.data.status || {};
        var history = json.data.history || [];

        // 导航栏指示灯
        if (indicator) {
            indicator.innerHTML = '<span class="dot" style="background:' + escapeHtml(s.color || '#3fb950') + '"></span>'
                + '<span style="color:' + escapeHtml(s.color || '#3fb950') + '">' + escapeHtml(s.label || '正常') + '</span>';
        }

        var html = '<div class="alert-status-row">';
        html += '<div class="alert-big" style="border-color:' + escapeHtml(s.color || '#3fb950') + '">';
        html += '<div class="alert-level" style="color:' + escapeHtml(s.color || '#3fb950') + '">' + escapeHtml(s.label || '正常') + '</div>';
        html += '<div class="alert-score">' + escapeHtml(formatNumber(s.risk_score, 1)) + '</div>';
        html += '<div class="alert-desc">' + escapeHtml(s.description || '') + '</div>';
        html += '</div>';
        html += '<div class="alert-meta">';
        html += '<div>近24h活跃预警: <b>' + escapeHtml(String(s.active_alerts || 0)) + '</b></div>';
        html += '</div></div>';

        // 预警历史
        if (history.length > 0) {
            html += '<div class="alert-history"><div class="ah-title">预警历史 (最近' + history.length + '条)</div>';
            history.slice(0, 8).forEach(function (a) {
                html += '<div class="ah-item"><span class="dot" style="background:' + escapeHtml(a.color || '#888') + '"></span>'
                    + '<span class="ah-label">' + escapeHtml(a.label || '') + '</span>'
                    + '<span class="ah-score">' + escapeHtml(formatNumber(a.risk_score, 1)) + '分</span>'
                    + '<span class="ah-time">' + escapeHtml((a.triggered_at || '').slice(0, 16).replace('T', ' ')) + '</span></div>';
            });
            html += '</div>';
        } else {
            html += '<div class="muted-note">暂无预警记录，系统运行正常。</div>';
        }
        panel.innerHTML = html;
    } catch (e) {
        hideLoading(panel);
        panel.innerHTML = errBox(e.message);
    }
}

/* ================= 地缘位势 + 空间自相关 ================= */
async function loadGeoPotential() {
    var geoEl = document.getElementById('geo-body');
    var acEl = document.getElementById('autocorr-body');
    renderLoading(geoEl);
    renderLoading(acEl);
    try {
        var json = await fetchJSON('/api/geo_potential');
        hideLoading(geoEl); hideLoading(acEl);
        if (!json.success) { geoEl.innerHTML = errBox(json.error); return; }
        var d = json.data;

        // 位势 Top5
        var top = (d.potential && d.potential.provinces || []).slice(0, 6);
        var html = '<div class="muted-note">' + escapeHtml(d.potential.model || '') + '</div>';
        top.forEach(function (p) {
            html += metricBarHTML(
                p.province + ' (' + escapeHtml(p.dominant_center || '') + ')',
                p.potential_normalized, 100
            );
        });
        geoEl.innerHTML = html;

        // 空间自相关
        var ac = d.spatial_autocorrelation || {};
        var moran = ac.morans_i || 0;
        var moranColor = moran > 0.3 ? 'var(--risk-high)' : moran > 0.1 ? 'var(--risk-medium)' : 'var(--accent-blue)';
        var acHtml = '<div class="moran-box">';
        acHtml += '<div class="moran-val" style="color:' + moranColor + '">' + escapeHtml(formatNumber(moran, 3)) + '</div>';
        acHtml += '<div class="moran-label">Moran\'s I 指数</div></div>';
        acHtml += '<div class="moran-interp">' + escapeHtml(ac.interpretation || '') + '</div>';

        // 热点
        var hotspots = d.hotspots || [];
        if (hotspots.length > 0) {
            acHtml += '<div class="hotspot-title">🔥 风险热点区</div>';
            hotspots.slice(0, 5).forEach(function (h) {
                acHtml += '<div class="hotspot-item"><span class="entity-tag location">' + escapeHtml(h.province) + '</span>'
                    + '<span class="muted-note">' + escapeHtml(h.cluster_type) + ' · 邻域均值 ' + escapeHtml(formatNumber(h.neighbor_avg_risk, 0)) + '</span></div>';
            });
        } else {
            acHtml += '<div class="muted-note">未识别到显著风险聚集区。</div>';
        }
        acEl.innerHTML = acHtml;
    } catch (e) {
        hideLoading(geoEl); hideLoading(acEl);
        geoEl.innerHTML = errBox(e.message);
    }
}

/* ================= 关系网络 ================= */
async function loadNetwork() {
    var el = document.getElementById('network-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/network');
        hideLoading(el);
        if (!json.success) { el.innerHTML = errBox(json.error); return; }
        var d = json.data;

        var html = '<div class="net-stats">';
        html += statChip('节点', d.node_count);
        html += statChip('关系', d.edge_count);
        html += statChip('密度', formatNumber(d.density, 3));
        html += statChip('社区', (d.communities || []).length);
        html += '</div>';

        // 关键行为体
        var actors = d.top_actors || [];
        if (actors.length > 0) {
            html += '<div class="sub-title">关键行为体 (度中心性)</div>';
            actors.slice(0, 6).forEach(function (a) {
                html += metricBarHTML(a.name + ' [' + escapeHtml(a.role || '') + ']', a.degree_centrality, 1);
            });
        }
        el.innerHTML = html;
    } catch (e) {
        hideLoading(el);
        el.innerHTML = errBox(e.message);
    }
}

/* ================= 诊断归因 ================= */
async function loadDiagnostic() {
    var el = document.getElementById('diagnostic-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/diagnostic');
        hideLoading(el);
        if (!json.success) { el.innerHTML = errBox(json.error); return; }
        var d = json.data;

        if (d.error) { el.innerHTML = '<div class="muted-note">' + escapeHtml(d.error) + '</div>'; return; }

        var html = '';
        // 变化摘要
        var delta = d.delta || 0;
        var deltaColor = delta > 0 ? 'var(--risk-high)' : delta < 0 ? 'var(--risk-low)' : 'var(--text-muted)';
        html += '<div class="diag-summary">';
        html += '<span class="diag-delta" style="color:' + deltaColor + '">' + (delta > 0 ? '▲' : delta < 0 ? '▼' : '—') + ' ' + escapeHtml(formatNumber(Math.abs(delta), 1)) + '</span>';
        html += '<span class="muted-note"> 分 (' + escapeHtml(d.trend || '') + ')</span>';
        html += '</div>';
        html += '<div class="diag-text">' + escapeHtml(d.change_text || '') + '</div>';

        // 各因素变化
        var changes = d.changes || [];
        if (changes.length > 0) {
            html += '<div class="sub-title">驱动因素变化</div>';
            changes.slice(0, 5).forEach(function (c) {
                var cColor = c.change > 0 ? 'var(--risk-high)' : 'var(--risk-low)';
                html += '<div class="change-item"><span>' + escapeHtml(c.name) + '</span>'
                    + '<span style="color:' + cColor + '">' + (c.change > 0 ? '+' : '') + escapeHtml(formatNumber(c.change, 1)) + '</span></div>';
            });
        }
        if (d.recent_period) {
            html += '<div class="muted-note" style="margin-top:0.5rem">对比: ' + escapeHtml(d.older_period) + ' → ' + escapeHtml(d.recent_period) + '</div>';
        }
        el.innerHTML = html;
    } catch (e) {
        hideLoading(el);
        el.innerHTML = errBox(e.message);
    }
}

/* ================= 多源融合视图 ================= */
async function loadMultimodal() {
    var chartEl = document.getElementById('multimodal-chart');
    var corrEl = document.getElementById('correlation-body');
    try {
        var json = await fetchJSON('/api/multimodal');
        if (!json.success) { corrEl.innerHTML = errBox(json.error); return; }
        var d = json.data;
        var aligned = d.aligned || [];

        var months = aligned.map(function (a) { return a.month; });
        var nightlight = aligned.map(function (a) { return a.nightlight; });
        var conflict = aligned.map(function (a) { return a.conflict_count; });
        var sentiment = aligned.map(function (a) { return a.sentiment_avg; });

        renderMultimodalChart(chartEl, months, nightlight, conflict, sentiment);

        // 相关性
        var corr = d.correlations || {};
        var html = '<div class="corr-row">';
        html += corrChip('夜光×冲突', corr.nightlight_vs_conflict);
        html += corrChip('夜光×情感', corr.nightlight_vs_sentiment);
        html += corrChip('冲突×情感', corr.conflict_vs_sentiment);
        html += '</div>';
        corrEl.innerHTML = html;
    } catch (e) {
        corrEl.innerHTML = errBox(e.message);
    }
}

function renderMultimodalChart(dom, months, nightlight, conflict, sentiment) {
    if (!mmChart) {
        mmChart = echarts.init(dom);
        window.addEventListener('resize', function () { mmChart.resize(); });
    }
    var axisStyle = {
        axisLine: { lineStyle: { color: '#2a2d35' } },
        axisLabel: { color: '#8a8f98', fontSize: 10 },
        splitLine: { lineStyle: { color: '#1e2128', type: 'dashed' } }
    };
    mmChart.setOption({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(26,29,35,0.95)', borderColor: '#2a2d35', textStyle: { color: '#e0e0e0' } },
        legend: { data: ['夜光指数', '冲突次数', '情感风险'], top: 0, textStyle: { color: '#8a8f98' } },
        grid: { left: '3%', right: '4%', bottom: '3%', top: 40, containLabel: true },
        xAxis: Object.assign({ type: 'category', data: months }, axisStyle),
        yAxis: [
            Object.assign({ type: 'value', name: '归一化', min: 0, max: 1, nameTextStyle: { color: '#8a8f98' } }, axisStyle),
            Object.assign({ type: 'value', name: '冲突', nameTextStyle: { color: '#8a8f98' } }, axisStyle)
        ],
        series: [
            { name: '夜光指数', type: 'line', smooth: true, data: nightlight, itemStyle: { color: '#f0c000' }, areaStyle: { opacity: 0.1 } },
            { name: '冲突次数', type: 'bar', yAxisIndex: 1, data: conflict, itemStyle: { color: 'rgba(248,81,73,0.6)' } },
            { name: '情感风险', type: 'line', smooth: true, data: sentiment, itemStyle: { color: '#1e90ff' } }
        ]
    });
}

/* ================= 历史事件时间线 ================= */
async function loadHistory() {
    var el = document.getElementById('history-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/history?severity_min=4');
        hideLoading(el);
        if (!json.success) { el.innerHTML = errBox(json.error); return; }
        var events = (json.data.events || []).slice().reverse();
        var stats = json.data.stats || {};

        var html = '<div class="net-stats">';
        html += statChip('事件总数', stats.total_events);
        html += statChip('平均烈度', formatNumber(stats.avg_severity, 1));
        html += '</div>';

        html += '<div class="timeline">';
        events.slice(0, 15).forEach(function (ev) {
            var sevColor = ev.severity >= 5 ? 'var(--risk-high)' : 'var(--risk-medium)';
            html += '<div class="tl-item">'
                + '<div class="tl-dot" style="background:' + sevColor + '"></div>'
                + '<div class="tl-content"><div class="tl-date">' + escapeHtml(ev.date) + ' · <span class="entity-tag event">' + escapeHtml(ev.event_type) + '</span></div>'
                + '<div class="tl-desc">' + escapeHtml(ev.description) + '</div>'
                + '<div class="tl-meta">📍 ' + escapeHtml(ev.location) + ' · 烈度 ' + escapeHtml(String(ev.severity)) + '/5</div></div></div>';
        });
        html += '</div>';
        el.innerHTML = html;
    } catch (e) {
        hideLoading(el);
        el.innerHTML = errBox(e.message);
    }
}

/* ================= 辅助函数 ================= */
function errBox(msg) {
    return '<div class="error-card"><div class="error-icon">⚠️</div><div class="error-msg">' + escapeHtml(msg || '加载失败') + '</div></div>';
}
function statChip(label, value) {
    return '<div class="stat-chip"><div class="sc-value">' + escapeHtml(String(value != null ? value : 'N/A')) + '</div><div class="sc-label">' + escapeHtml(label) + '</div></div>';
}
function corrChip(label, value) {
    if (value === null || value === undefined) value = 0;
    var color = Math.abs(value) > 0.5 ? 'var(--risk-high)' : Math.abs(value) > 0.3 ? 'var(--risk-medium)' : 'var(--text-muted)';
    return '<div class="corr-chip"><div class="cc-value" style="color:' + color + '">' + escapeHtml(formatNumber(value, 2)) + '</div><div class="cc-label">' + escapeHtml(label) + '</div></div>';
}
