/**
 * 综合态势仪表盘 JS
 * 依赖: common.js + ECharts (static/vendor/echarts)
 * 整合: 预警 / 地缘位势 / 空间自相关 / 关系网络 / 诊断归因 / 多源融合 / 历史时间线
 */

var mmChart = null;
var multimodalSequence = 0;
var alertSequence = 0;
var diagnosticSequence = 0;
var networkSequence = 0;
var dashboardSequence = 0;
var dashboardSnapshot = null;
var researchSequence = -1;

document.addEventListener('DOMContentLoaded', function () {
    initViewFilters(loadAll);
    document.getElementById('research-details').addEventListener('toggle', function () {
        if (this.open) { loadResearch(); if (mmChart) mmChart.resize(); }
    });
    loadAll();
});

async function loadAll() {
    var sequence = ++dashboardSequence;
    dashboardSnapshot = null;
    ++alertSequence; ++networkSequence; ++diagnosticSequence; ++multimodalSequence;
    setViewReport(null);
    if (mmChart) mmChart.clear();
    var ids = ['overview-body', 'focus-events', 'alert-panel', 'source-health-body', 'geo-body', 'autocorr-body', 'network-body', 'diagnostic-body', 'correlation-body', 'history-body'];
    ids.forEach(id => renderLoading(document.getElementById(id)));
    document.getElementById('alert-indicator').textContent = '预警状态待更新';
    var filters = readViewFilters();
    try {
        var response = await fetchJSON('/api/report?' + viewQuery({format:'json'}, filters));
        if (sequence !== dashboardSequence) return;
        if (!response.success) throw new Error(response.error || '快照不可用');
        dashboardSnapshot = response.data;
        renderOverview(dashboardSnapshot);
        setViewReport(dashboardSnapshot);
        syncViewNavigation(dashboardSnapshot.filters);
        window.history.replaceState(null, '', '/dashboard?' + viewQuery({}, dashboardSnapshot.filters));
        loadAlert();
        if (document.getElementById('research-details').open) loadResearch();
    } catch (e) {
        if (sequence !== dashboardSequence) return;
        ids.forEach(id => { document.getElementById(id).innerHTML = errBox(e.message); });
        document.getElementById('alert-indicator').textContent = '当前快照不可用';
    }
}

function dashboardQuery(extra) {
    return viewQuery(Object.assign({revision:dashboardSnapshot.revision}, extra || {}), dashboardSnapshot.filters);
}

function loadResearch() {
    if (!dashboardSnapshot || researchSequence === dashboardSequence) return;
    researchSequence = dashboardSequence;
    loadSourceHealth(); loadGeoPotential(); loadNetwork(); loadDiagnostic(); loadMultimodal(); loadHistory();
}

function renderOverview(snapshot) {
    var history = snapshot.history || [], meta = snapshot.metadata || {};
    var latest = history.length ? history[history.length - 1] : null;
    var current = latest && latest.date === snapshot.filters.end_date;
    var yesterday = new Date(snapshot.filters.end_date + 'T00:00:00Z');
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    var previous = history.find(r => r.date === yesterday.toISOString().slice(0, 10));
    var delta = current && previous ? latest.risk_score - previous.risk_score : null;
    var html = '<div class="net-stats">' + statChip('截止日风险（0～100）', current ? formatNumber(latest.risk_score, 1) : '无当前观测')
        + statChip('较前一日变化（分）', formatNumber(delta, 1))
        + statChip('有效观测日', (meta.valid_days || 0) + '/' + snapshot.filters.days)
        + statChip('日风险覆盖率', formatPercent(meta.coverage)) + '</div>';
    html += '<p class="muted-note">最新风险观测：' + escapeHtml(meta.actual_end || '无')
        + '；实际来源：' + escapeHtml((snapshot.sources || []).join('、') || '无') + '</p>';
    html += '<p class="muted-note">算法：' + escapeHtml(meta.algorithm_version || '') + '；快照：' + escapeHtml(snapshot.snapshot_id)
        + '；生成时间：' + escapeHtml(snapshot.generated_at) + '</p>';
    (snapshot.warnings || []).forEach(w => { html += '<p class="muted-note">' + escapeHtml(w) + '</p>'; });
    var indicators = latest && latest.details && latest.details.indicator_scores || {};
    var names = {conflict_frequency:'冲突报道频次', sentiment_avg:'情感风险', nightlight_change:'夜光变化', refugee_change:'难民变化', llm_risk:'LLM风险'};
    html += '<h4>已保存的指标贡献' + (latest ? '（' + escapeHtml(latest.date) + '）' : '') + '</h4>';
    Object.entries(indicators).filter(p => p[1].contribution != null).sort((a,b) => b[1].contribution - a[1].contribution).forEach(function (pair) {
        html += metricBarHTML(names[pair[0]] || pair[0], pair[1].contribution * 100, 100);
    });
    html += '<p class="muted-note">缺测不等于低风险；覆盖率不是采集完整率；贡献不表示因果。</p>';
    document.getElementById('overview-body').innerHTML = html;
    var events = (snapshot.events || []).slice().sort((a,b) => String(b.date).localeCompare(String(a.date)));
    var eventHTML = '<p class="muted-note">去重事件记录 ' + events.length + ' 条；不是已验证的独立现实事件数量。</p>';
    events.slice(0, 8).forEach(function (event) {
        eventHTML += '<div class="tl-item"><div class="tl-content"><b>' + escapeHtml(event.date) + ' · '
            + escapeHtml(event.event_location || event.location || '未定位') + '</b><p>'
            + escapeHtml(event.description || event.event_type || event.root_code || '事件类型未标注') + '</p><p class="muted-note">来源：'
            + escapeHtml(event.source || 'gdelt') + '；定位方法：' + escapeHtml(event.location_method || (event.lat != null && event.lon != null ? '坐标待边界匹配' : '未提供')) + '</p>';
        var url = event.source_url || event.url;
        if (typeof url === 'string' && /^https?:\/\//i.test(url)) eventHTML += '<a target="_blank" rel="noopener noreferrer" href="' + escapeHtml(url) + '">查看来源证据</a>';
        eventHTML += '</div></div>';
    });
    if (!events.length) eventHTML += '<p class="muted-note">所选窗口无事件记录，未回填旧闻或演示事件。</p>';
    document.getElementById('focus-events').innerHTML = eventHTML;
}

/* ================= 数据源健康 ================= */
async function loadSourceHealth() {
    var sequence = dashboardSequence;
    var el = document.getElementById('source-health-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/sources/health?' + dashboardQuery());
        if (sequence !== dashboardSequence) return;
        hideLoading(el);
        if (!json.success) { el.innerHTML = errBox(json.error); return; }
        var data = json.data || {};
        var names = Object.keys(data);
        if (names.length === 0) {
            el.innerHTML = '<div class="muted-note">暂无采集记录，健康状态将在首轮爬取后呈现。</div>';
            return;
        }

        var statusMeta = {
            healthy: { label: '健康', color: 'var(--risk-low)' },
            degraded: { label: '降级', color: 'var(--risk-medium)' },
            dead: { label: '失联', color: 'var(--risk-high)' }
        };

        var html = '<div class="sh-grid">';
        names.forEach(function (name) {
            var s = data[name];
            var meta = statusMeta[s.status] || statusMeta.degraded;
            html += '<div class="sh-item">'
                + '<div class="sh-head"><span class="dot" style="background:' + meta.color + '"></span>'
                + '<span class="sh-name">' + escapeHtml(name) + '</span>'
                + '<span class="sh-status" style="color:' + meta.color + '">' + meta.label + '</span></div>'
                + '<div class="sh-meta">成功率 ' + escapeHtml(formatNumber(s.success_rate * 100, 0)) + '% ('
                + escapeHtml(String(s.recent_success)) + '/' + escapeHtml(String(s.recent_attempts)) + ')'
                + ' · 上轮 ' + escapeHtml(String(s.last_count)) + ' 条</div>';
            if (s.last_error) {
                html += '<div class="sh-err">' + escapeHtml(s.last_error) + '</div>';
            }
            html += '</div>';
        });
        html += '</div>';
        el.innerHTML = html;
    } catch (e) {
        if (sequence !== dashboardSequence) return;
        hideLoading(el);
        el.innerHTML = errBox(e.message);
    }
}

/* ================= 预警面板 ================= */
async function loadAlert() {
    var sequence = ++alertSequence;
    var panel = document.getElementById('alert-panel');
    var indicator = document.getElementById('alert-indicator');
    renderLoading(panel);
    try {
        if (dashboardSnapshot.filters.source) {
            panel.textContent = '单来源子集未重算综合指标，不产生正式预警';
            if (indicator) indicator.textContent = '单来源筛选不适用正式预警';
            return;
        }
        var json = await fetchJSON('/api/alert?' + dashboardQuery());
        if (sequence !== alertSequence) return;
        hideLoading(panel);
        if (!json.success) { panel.innerHTML = errBox(json.error); return; }
        var s = json.data.status || {};
        var history = json.data.history || [];

        // 导航栏指示灯
        if (indicator) {
            indicator.innerHTML = '<span class="dot" style="background:' + escapeHtml(s.color || '#8b949e') + '"></span>'
                + '<span style="color:' + escapeHtml(s.color || '#8b949e') + '">' + escapeHtml(s.label || '状态未知') + '</span>';
        }

        var html = '<div class="alert-status-row">';
        html += '<div class="alert-big" style="border-color:' + escapeHtml(s.color || '#8b949e') + '">';
        html += '<div class="alert-level" style="color:' + escapeHtml(s.color || '#8b949e') + '">' + escapeHtml(s.label || '状态未知') + '</div>';
        html += '<div class="alert-score">' + escapeHtml(formatNumber(s.risk_score, 1)) + '</div>';
        html += '<div class="alert-desc">' + escapeHtml(s.description || '') + '</div>';
        html += '</div>';
        html += '<div class="alert-meta">';
        html += '<div>当前未确认预警: <b>' + escapeHtml(String(s.active_alerts || 0)) + '</b></div>';
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
            html += '<div class="muted-note">暂无可确认的预警记录；缺测不能解释为低风险。</div>';
        }
        panel.innerHTML = html;
    } catch (e) {
        if (sequence !== alertSequence) return;
        hideLoading(panel);
        if (indicator) indicator.textContent = '预警状态获取失败';
        panel.innerHTML = errBox(e.message);
    }
}

/* ================= 地缘位势 + 空间自相关 ================= */
async function loadGeoPotential() {
    var sequence = dashboardSequence;
    var geoEl = document.getElementById('geo-body');
    var acEl = document.getElementById('autocorr-body');
    renderLoading(geoEl);
    renderLoading(acEl);
    try {
        var json = await fetchJSON('/api/geo_potential?' + dashboardQuery());
        if (sequence !== dashboardSequence) return;
        hideLoading(geoEl); hideLoading(acEl);
        if (!json.success) { geoEl.innerHTML = acEl.innerHTML = errBox(json.error); return; }
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
        var moran = ac.morans_i == null ? null : ac.morans_i;
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
            acHtml += '<div class="muted-note">当前未提供可报告热点；样本不足不能解释为不存在聚集。</div>'; 
        }
        acEl.innerHTML = acHtml;
    } catch (e) {
        if (sequence !== dashboardSequence) return;
        hideLoading(geoEl); hideLoading(acEl);
        geoEl.innerHTML = acEl.innerHTML = errBox(e.message);
    }
}

/* ================= 关系网络 ================= */
async function loadNetwork() {
    var sequence = ++networkSequence;
    var el = document.getElementById('network-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/network?' + dashboardQuery());
        if (sequence !== networkSequence) return;
        hideLoading(el);
        if (!json.success) { el.innerHTML = errBox(json.error); return; }
        var d = json.data;
        var meta = d.metadata || {};
        if (d.error) { el.innerHTML = '<div class="muted-note">' + escapeHtml(d.error) + '</div>'; return; }
        var html = '<p class="muted-note">' + escapeHtml(meta.relation_semantics || meta.warning || '') + '</p>';
        html += '<div class="net-stats">';
        html += statChip('节点', d.node_count);
        html += statChip('共现实体对', d.edge_count);
        html += statChip('证据文章', meta.article_count);
        html += statChip('密度', formatNumber(d.density, 3));
        html += statChip('社区', (d.communities || []).length);
        html += '</div>';

        // 关键行为体
        var actors = d.top_actors || [];
        if (actors.length > 0) {
            html += '<div class="sub-title">共现网络高连接实体（非现实影响力排名）</div>';
            actors.slice(0, 6).forEach(function (a) {
                html += metricBarHTML(a.name + ' [' + escapeHtml(a.role || '') + ']', a.degree_centrality, 1);
            });
        }
        el.innerHTML = html;
    } catch (e) {
        if (sequence !== networkSequence) return;
        hideLoading(el);
        el.innerHTML = errBox(e.message);
    }
}

/* ================= 诊断归因 ================= */
async function loadDiagnostic() {
    var sequence = ++diagnosticSequence;
    var el = document.getElementById('diagnostic-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/diagnostic?' + dashboardQuery());
        if (sequence !== diagnosticSequence) return;
        hideLoading(el);
        if (!json.success) { el.innerHTML = errBox(json.error); return; }
        var d = json.data;

        if (d.error) { el.innerHTML = '<div class="muted-note">' + escapeHtml(d.error) + '</div>'; return; }

        var html = '';
        // 变化摘要
        var delta = d.delta == null ? null : d.delta;
        var deltaColor = delta > 0 ? 'var(--risk-high)' : delta < 0 ? 'var(--risk-low)' : 'var(--text-muted)';
        html += '<div class="diag-summary">';
        html += '<span class="diag-delta" style="color:' + deltaColor + '">' + (delta > 0 ? '▲' : delta < 0 ? '▼' : '—') + ' ' + escapeHtml(formatNumber(delta == null ? null : Math.abs(delta), 1)) + '</span>';
        html += '<span class="muted-note"> 分 (' + escapeHtml(d.trend || '') + ')</span>';
        html += '</div>';
        html += '<div class="diag-text">' + escapeHtml(d.change_text || '') + '</div>';

        // 各因素变化
        var changes = d.changes || [];
        if (changes.length > 0) {
            html += '<div class="sub-title">实际指标贡献变化（分）</div>';
            changes.slice(0, 5).forEach(function (c) {
                var cColor = c.change > 0 ? 'var(--risk-high)' : 'var(--risk-low)';
                html += '<div class="change-item"><span>' + escapeHtml(c.name) + '</span>'
                    + '<span style="color:' + cColor + '">' + (c.change > 0 ? '+' : '') + escapeHtml(formatNumber(c.change, 1)) + '</span></div>';
            });
        }
        if (d.recent_period) {
            html += '<div class="muted-note" style="margin-top:0.5rem">对比: ' + escapeHtml(d.older_period) + ' → ' + escapeHtml(d.recent_period) + '</div>';
        }

        // 上升原因详解
        var rx = d.rise_explanation;
        if (rx && rx.detail && rx.detail.length > 0) {
            html += '<div class="sub-title" style="margin-top:0.6rem">贡献变化详情</div>';
            html += '<div class="diag-text">' + escapeHtml(rx.text || '') + '</div>';
            rx.detail.slice(0, 4).forEach(function (c) {
                var cColor = c.change > 0 ? 'var(--risk-high)' : 'var(--risk-low)';
                html += '<div class="change-item"><span>' + escapeHtml(c.name)
                    + ' <span class="muted-note">' + escapeHtml(formatNumber(c.value_older, 2)) + '→' + escapeHtml(formatNumber(c.value_recent, 2)) + '</span></span>'
                    + '<span style="color:' + cColor + '">' + (c.change > 0 ? '+' : '') + escapeHtml(formatNumber(c.change, 2)) + '</span></div>';
            });
        }

        // 未来风险预警
        var fo = d.future_outlook;
        if (fo) {
            var lvlColor = fo.projected_level === 'red' ? 'var(--risk-high)'
                : fo.projected_level === 'orange' ? 'var(--risk-medium)'
                : fo.projected_level === 'yellow' ? '#e3b341' : 'var(--text-muted)';
            html += '<div class="sub-title" style="margin-top:0.6rem">未来 ' + escapeHtml(String(fo.days_ahead || 7)) + ' 天探索性外推</div>';
            html += '<div class="diag-summary"><span class="diag-delta" style="color:' + lvlColor + '">' + escapeHtml(formatNumber(fo.predicted_score, 1)) + '</span>'
                + '<span class="muted-note"> 分 → ' + escapeHtml(fo.projected_label || '') + '（' + escapeHtml(fo.reliability || '未验收') + '）</span></div>';
            html += '<div class="diag-text">' + escapeHtml(fo.text || '') + '</div>';
            if (fo.leading_signals && fo.leading_signals.length > 0) {
                html += '<div class="muted-note">先行信号: ' + fo.leading_signals.map(function (s) { return escapeHtml(s); }).join('、') + '</div>';
            }
        }
        el.innerHTML = html;
    } catch (e) {
        if (sequence !== diagnosticSequence) return;
        hideLoading(el);
        el.innerHTML = errBox(e.message);
    }
}

/* ================= 多源融合视图 ================= */
async function loadMultimodal() {
    var sequence = ++multimodalSequence;
    var chartEl = document.getElementById('multimodal-chart');
    var corrEl = document.getElementById('correlation-body');
    try {
        var json = await fetchJSON('/api/multimodal?' + dashboardQuery());
        if (sequence !== multimodalSequence) return;
        if (!json.success) { if (mmChart) mmChart.clear(); corrEl.innerHTML = errBox(json.error); return; }
        var d = json.data;
        var aligned = d.aligned || [];
        var hasMonthly = aligned.some(a => a.nightlight != null || a.conflict_count != null || a.sentiment_avg != null);
        var seriesKeys = new Set(aligned.filter(a => a.nightlight != null).map(a => a.nightlight_series_key));
        if (hasMonthly) renderMultimodalChart(chartEl, aligned, seriesKeys.size > 1);
        else if (mmChart) mmChart.clear();

        // 相关性
        var corr = d.correlations || {};
        var html = hasMonthly ? '' : '<p class="muted-note">所选窗口没有完整月份的有效多模态观测；未合成或复制年度数据。</p>';
        html += '<div class="corr-row">';
        html += corrChip('夜光×冲突', corr.nightlight_vs_conflict);
        html += corrChip('夜光×情感', corr.nightlight_vs_sentiment);
        html += corrChip('冲突×情感', corr.conflict_vs_sentiment);
        html += '</div><div class="muted-note">至少12个独立配对月观测才计算相关；缺失不等于零。年度代理不展开为月度遥感。</div>';
        var counts = corr.sample_counts || {};
        html += '<div class="muted-note">配对样本：夜光×冲突 ' + escapeHtml(String(counts.nightlight_vs_conflict || 0))
            + '；夜光×情感 ' + escapeHtml(String(counts.nightlight_vs_sentiment || 0))
            + '；冲突×情感 ' + escapeHtml(String(counts.conflict_vs_sentiment || 0)) + '</div>';
        var labels = {nightlight_vs_conflict:'夜光变化×冲突', nightlight_vs_sentiment:'夜光变化×情感', conflict_vs_sentiment:'冲突×情感'};
        Object.entries(corr.reasons || {}).forEach(function (pair) {
            html += '<p class="muted-note">' + escapeHtml(labels[pair[0]] || pair[0]) + '：' + escapeHtml(pair[1]) + '</p>';
        });
        if (seriesKeys.size > 1) html += '<p class="muted-note">夜光来源、量纲或版本发生变化，未连成同一曲线；请查看下方原值。</p>';
        html += '<details><summary>查看原值、单位、完整周期、来源与质量</summary><div class="observation-table"><table><thead><tr><th>周期（结束排他）</th><th>夜光原值 / 单位</th><th>冲突类记录数</th><th>情感风险0～1</th><th>来源与质量</th></tr></thead><tbody>';
        aligned.forEach(function (a) {
            html += '<tr><td>' + escapeHtml(a.period_start) + ' → ' + escapeHtml(a.period_end) + '</td><td>'
                + escapeHtml(formatNumber(a.nightlight, 4)) + ' / ' + escapeHtml((a.units || {}).nightlight || '无单位观测') + '</td><td>'
                + escapeHtml(formatNumber(a.conflict_count, 0)) + '</td><td>' + escapeHtml(formatNumber(a.sentiment_avg, 3)) + '</td><td>'
                + escapeHtml((a.sources || []).join('、') || '无') + '；夜光 ' + escapeHtml((a.quality || {}).nightlight)
                + '；' + escapeHtml((a.warnings || []).join('；')) + '</td></tr>';
        });
        html += '</tbody></table></div></details>';
        var annual = d.annual || {};
        html += '<section id="annual-observations"><h4>年度观测原值</h4><p class="muted-note">'
            + escapeHtml(annual.note || '年度观测独立展示，不参与月度相关') + '</p>';
        if ((annual.observations || []).length) {
            html += '<div class="observation-table"><table><thead><tr><th>指标 / 周期（结束排他）</th><th>原值 / 单位</th><th>来源 / 产品 / 版本</th></tr></thead><tbody>';
            annual.observations.forEach(function (a) {
                html += '<tr><td>' + escapeHtml(a.indicator) + '<br>' + escapeHtml(a.period_start) + ' → '
                    + escapeHtml(a.period_end) + '</td><td>' + escapeHtml(formatNumber(a.value, 4)) + ' / '
                    + escapeHtml(a.unit) + '</td><td>' + escapeHtml(a.source) + ' / ' + escapeHtml(a.product || '未注明产品')
                    + ' / ' + escapeHtml(a.dataset_version) + '</td></tr>';
            });
            html += '</tbody></table></div>';
        } else html += '<p class="muted-note">窗口内无完整年度的真实观测。</p>';
        if (annual.invalid_count) html += '<p class="muted-note">已隔离不合格年度记录：' + escapeHtml(String(annual.invalid_count)) + '</p>';
        html += '</section>';
        corrEl.innerHTML = html;
    } catch (e) {
        if (sequence !== multimodalSequence) return;
        if (mmChart) mmChart.clear();
        corrEl.innerHTML = errBox(e.message);
    }
}

function renderMultimodalChart(dom, aligned, mixedNightlight) {
    var months = aligned.map(a => a.month);
    var nightlight = aligned.map(a => mixedNightlight ? null : a.nightlight);
    var conflict = aligned.map(a => a.conflict_count);
    var sentiment = aligned.map(a => a.sentiment_avg);
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
        legend: { data: ['夜光原值', '冲突类记录数', '情感风险'], top: 0, textStyle: { color: '#8a8f98' } },
        grid: [
            { left: 65, right: 20, top: '16%', height: '18%' },
            { left: 65, right: 20, top: '46%', height: '18%' },
            { left: 65, right: 20, top: '76%', height: '16%' }
        ],
        xAxis: [0, 1, 2].map(function (i) { return Object.assign({}, axisStyle, { type: 'category', data: months, gridIndex: i, axisLabel: { show: i === 2, color: '#8a8f98' } }); }),
        yAxis: [
            Object.assign({ gridIndex: 0, type: 'value', name: '夜光原值', nameTextStyle: { color: '#8a8f98' } }, axisStyle),
            Object.assign({ gridIndex: 1, type: 'value', name: '冲突类记录数', minInterval: 1, nameTextStyle: { color: '#8a8f98' } }, axisStyle),
            Object.assign({ gridIndex: 2, type: 'value', name: '情感0～1', min: 0, max: 1, nameTextStyle: { color: '#8a8f98' } }, axisStyle)
        ],
        series: [
            { name: '夜光原值', type: 'line', xAxisIndex: 0, yAxisIndex: 0, connectNulls: false, data: nightlight, itemStyle: { color: '#f0c000' } },
            { name: '冲突类记录数', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: conflict, itemStyle: { color: 'rgba(248,81,73,0.6)' } },
            { name: '情感风险', type: 'line', xAxisIndex: 2, yAxisIndex: 2, connectNulls: false, data: sentiment, itemStyle: { color: '#1e90ff' } }
        ]
    }, { notMerge: true });
}

/* ================= 历史事件时间线 ================= */
async function loadHistory() {
    var sequence = dashboardSequence;
    var el = document.getElementById('history-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/history?severity_min=4');
        if (sequence !== dashboardSequence) return;
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
        if (sequence !== dashboardSequence) return;
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
    if (value === null || value === undefined || !Number.isFinite(value)) {
        return '<div class="corr-chip"><div class="cc-value">不可报告</div><div class="cc-label">' + escapeHtml(label) + '</div></div>';
    }
    var color = Math.abs(value) > 0.5 ? 'var(--risk-high)' : Math.abs(value) > 0.3 ? 'var(--risk-medium)' : 'var(--text-muted)';
    return '<div class="corr-chip"><div class="cc-value" style="color:' + color + '">' + escapeHtml(formatNumber(value, 2)) + '</div><div class="cc-label">' + escapeHtml(label) + '</div></div>';
}
