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

var researchView = '';
document.addEventListener('DOMContentLoaded', function () {
    researchView = document.body.dataset.researchView || '';
    if (!researchView) return;
    initViewFilters(loadAll);
    loadAll();
});

async function loadAll() {
    var sequence = ++dashboardSequence;
    dashboardSnapshot = null;
    ++alertSequence; ++networkSequence; ++diagnosticSequence; ++multimodalSequence;
    setViewReport(null);
    if (mmChart) mmChart.clear();
    var ids = ['overview-body', 'focus-events', 'alert-panel', 'geo-body', 'autocorr-body', 'diagnostic-body', 'correlation-body', 'spatial-detail', 'data-coverage', 'daily-records'];
    ids.forEach(id => renderLoading(document.getElementById(id)));
    if (researchView === 'history') { loadHistory(); return; }
    var filters = readViewFilters();
    var status = document.getElementById('view-status');
    if (status) status.textContent = '正在读取已保存观测…';
    if (researchView === 'data') loadSourceHealth();
    if (researchView === 'risk') {
        if (status) status.textContent = '窗口：' + filters.days + '天 · 截至 ' + filters.end_date + '；仅用于证据型窗口研判。';
        return;
    }
    try {
        var extra = {format:'json'};
        if (sequence === 1) extra.revision = new URLSearchParams(location.search).get('revision');
        var response = await fetchJSON('/api/report?' + viewQuery(extra, filters));
        if (sequence !== dashboardSequence) return;
        if (!response.success) throw new Error(response.error || '快照不可用');
        dashboardSnapshot = response.data;
        renderOverview(dashboardSnapshot);
        setViewReport(dashboardSnapshot);
        syncViewNavigation(dashboardSnapshot.filters);
        var selectedQuery = new URLSearchParams(location.search);
        var selection = selectedQuery.get('event_id');
        window.history.replaceState(null, '', location.pathname + '?' + viewQuery(selection ? {event_id:selection,event_source:selectedQuery.get('event_source')} : {}, dashboardSnapshot.filters));
        if (status) status.textContent = '截至 ' + dashboardSnapshot.filters.end_date + ' · ' + dashboardSnapshot.filters.days + '天 · 修订 ' + dashboardSnapshot.revision.slice(0, 12);
        if (researchView === 'data') renderDataCoverage(dashboardSnapshot);
        loadResearch();
    } catch (e) {
        if (sequence !== dashboardSequence) return;
        ids.forEach(id => { var node = document.getElementById(id); if (node) node.innerHTML = errBox(e.message); });
        if (status) status.textContent = '当前快照不可用；未显示旧筛选结果。';
    }
}

function dashboardQuery(extra) {
    return viewQuery(Object.assign({revision:dashboardSnapshot.revision}, extra || {}), dashboardSnapshot.filters);
}

function loadResearch() {
    if (!dashboardSnapshot || researchSequence === dashboardSequence) return;
    researchSequence = dashboardSequence;
    if (researchView === 'indicators') { loadAlert(); loadDiagnostic(); }
    if (researchView === 'spatial') loadGeoPotential();
    if (researchView === 'multimodal') loadMultimodal();
}

function renderOverview(snapshot) {
    var history = snapshot.history || [], meta = snapshot.metadata || {};
    var observed = history.filter(r => isScore(r.risk_score));
    var latest = observed.length ? observed[observed.length - 1] : null;
    var current = latest && latest.date === snapshot.filters.end_date;
    var yesterday = new Date(snapshot.filters.end_date + 'T00:00:00Z');
    yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    var previous = observed.find(r => r.date === yesterday.toISOString().slice(0, 10));
    var delta = current && sameRiskBasis(latest,previous) ? latest.risk_score - previous.risk_score : null;
    var html = '<div class="situation">' + situationGaugeHTML(latest, current, snapshot.filters.end_date)
        + '<div class="sit-side"><div class="net-stats sit-kpis">'
        + statChip('较前一日变化（分）', formatNumber(delta, 1))
        + statChip('有效观测日', (meta.valid_days || 0) + '/' + snapshot.filters.days)
        + statChip('日风险覆盖率', formatPercent(meta.coverage)) + '</div>'
        + riskStripHTML(observed, snapshot.filters.end_date, snapshot.filters.days) + '</div></div>';
    html += '<p class="muted-note">最新风险观测：' + escapeHtml(meta.actual_end || '无')
        + '；实际来源：' + escapeHtml((snapshot.sources || []).join('、') || '无') + '</p>';
    html += '<details class="muted-note"><summary>算法 ' + escapeHtml(meta.algorithm_version || '')
        + ' · 快照 ' + escapeHtml(String(snapshot.snapshot_id || '').slice(0, 12)) + '</summary>'
        + '<p>完整快照：' + escapeHtml(snapshot.snapshot_id) + '；生成时间：' + escapeHtml(snapshot.generated_at) + '</p></details>';
    (snapshot.warnings || []).forEach(w => { html += '<p class="muted-note">' + escapeHtml(w) + '</p>'; });
    var indicators = latest && latest.details && latest.details.indicator_scores || {};
    var names = {conflict_frequency:'冲突报道频次', sentiment_avg:'情感风险', nightlight_change:'夜光变化', refugee_change:'难民变化', event_severity:'事件严重度', llm_risk:'LLM风险'};
    html += '<h4>已保存的指标贡献' + (latest ? '（' + escapeHtml(latest.date) + '）' : '') + '</h4>';
    Object.entries(indicators).filter(p => p[1].contribution != null).sort((a,b) => b[1].contribution - a[1].contribution).forEach(function (pair) {
        html += metricBarHTML(names[pair[0]] || pair[0], pair[1].contribution * 100, 100);
    });
    html += '<p class="muted-note">缺测不等于低风险；覆盖率不是采集完整率；贡献不表示因果。</p>';
    var overview = document.getElementById('overview-body');
    if (overview) overview.innerHTML = html;
    const daily = document.getElementById('daily-records');
    if (daily) {
        const byDate = Object.fromEntries(history.map(row => [row.date,row]));
        const end = new Date(snapshot.filters.end_date + 'T00:00:00Z');
        let rows = '<table><thead><tr><th>业务日</th><th>风险分／100</th><th>来源</th><th>算法与观测质量</th></tr></thead><tbody>';
        for (let i=snapshot.filters.days-1;i>=0;i--) {
            const key = new Date(end.getTime()-i*86400000).toISOString().slice(0,10);
            const row = byDate[key];
            rows += '<tr><td>' + key + '</td><td>' + escapeHtml(formatNumber(row?.risk_score,1)) + '</td><td>'
                + escapeHtml((row?.sources || []).join('、') || '—') + '</td><td>'
                + escapeHtml(row ? (row.algorithm_version || row.details?.algorithm_version || '版本未提供') : '缺测，不补零') + '</td></tr>';
        }
        daily.innerHTML = rows + '</tbody></table>';
    }
    if (!document.getElementById('focus-events')) return;
    var events = (snapshot.events || []).slice().sort((a,b) => String(b.date).localeCompare(String(a.date)));
    var eventHTML = '<p class="muted-note">去重事件记录 ' + events.length + ' 条；不是已验证的独立现实事件数量。</p>';
    eventHTML += quadMixHTML(events);
    events.forEach(function (event, index) {
        var root = String(event.root_code || '').padStart(2, '0');
        var quad = String(event.quad_class || '');
        var kind = event.event_type || CAMEO_ROOT[root] || '事件类型未标注';
        var selectedQuery = new URLSearchParams(location.search);
        var selected = selectedQuery.get('event_id') === String(event.event_id) && (!selectedQuery.get('event_source') || selectedQuery.get('event_source')===event.source);
        eventHTML += '<div id="evidence-' + index + '" class="ev-item quad-' + (QUAD[quad] ? quad : '0') + (selected ? ' evidence-selected' : '') + '"><div class="ev-top"><span class="ev-tag">'
            + escapeHtml(kind) + '</span><span class="ev-date">' + escapeHtml(event.date) + '</span></div>'
            + '<div class="ev-loc">' + escapeHtml(event.event_location || event.location || '未定位') + '</div>';
        if (event.description) eventHTML += '<p class="ev-desc">' + escapeHtml(event.description) + '</p>';
        var meta = [];
        if (CAMEO_ROOT[root]) meta.push('CAMEO ' + root);
        if (event.num_articles != null && Number.isFinite(Number(event.num_articles))) meta.push('报道 ' + Number(event.num_articles) + ' 篇');
        meta.push('记录ID ' + (event.event_id || '—'));
        meta.push('来源 ' + (event.source || 'gdelt'));
        meta.push(event.evidence_verified === true && event.independent_source_count >= 2 ? '已审核独立多源' : '证据未核验（报道量不是独立互证）');
        meta.push(event.location_method || (event.lat != null && event.lon != null ? '坐标待边界匹配' : '未提供定位方法'));
        eventHTML += '<div class="ev-meta">' + escapeHtml(meta.join(' · '));
        var url = event.source_url || event.url;
        if (typeof url === 'string' && /^https?:\/\//i.test(url)) eventHTML += ' · <a target="_blank" rel="noopener noreferrer" href="' + escapeHtml(url) + '">查看来源证据</a>';
        eventHTML += '</div></div>';
    });
    if (!events.length) eventHTML += '<p class="muted-note">所选窗口无事件记录，未回填旧闻或演示事件。</p>';
    document.getElementById('focus-events').innerHTML = eventHTML;
    document.querySelector('.evidence-selected')?.scrollIntoView({block:'center'});
}

function renderDataCoverage(snapshot) {
    var latest = (snapshot.history || []).slice().reverse().find(r => isScore(r.risk_score));
    var details = latest?.details || {};
    var coverage = latest?.indicator_coverage ?? details.indicator_coverage;
    var counts = details.source_stances || latest?.source_stances || {};
    var labels = {domestic:'境内中文',independent:'独立媒体',ethnic:'民族媒体',international:'国际组织',aggregator:'聚合／事件库',official:'官方',military:'军方',unlabeled:'未标注'};
    var html = '<p>日风险观测：' + escapeHtml(String(snapshot.metadata.valid_days)) + '／' + snapshot.filters.days + '天；最新观测：' + escapeHtml(latest?.date || '—') + '</p>'
        + '<p>最新观测有效指标权重：' + escapeHtml(formatPercent(coverage)) + '；缺项按有效权重归一，不表示采集完整。</p>'
        + '<p>窗口来源：' + escapeHtml((snapshot.sources || []).join('、') || '—') + '</p>';
    html += '<p>最新日新闻立场构成：' + escapeHtml(Object.entries(counts).map(([k,v]) => (labels[k] || k) + ' ' + v + '条').join('；') || '该旧记录未保存立场构成，不能猜测') + '</p>';
    (snapshot.warnings || []).forEach(w => { html += '<p class="muted-note">' + escapeHtml(w) + '</p>'; });
    document.getElementById('data-coverage').innerHTML = html;
}

/* ================= 态势主视觉：风险仪表 + 逐日条码 ================= */
// CAMEO 根代码与四类（QuadClass）中文名，仅作报道类型说明，不是事件定性。
var CAMEO_ROOT = {'01': '公开声明', '02': '呼吁', '03': '表达合作意向', '04': '磋商', '05': '外交合作',
    '06': '物质合作', '07': '提供援助', '08': '让步', '09': '调查', '10': '要求', '11': '表示不满',
    '12': '拒绝', '13': '威胁', '14': '抗议', '15': '展示武力', '16': '降低关系', '17': '胁迫',
    '18': '袭击', '19': '交战', '20': '大规模暴力'};
var QUAD = {'1': '言语合作', '2': '物质合作', '3': '言语冲突', '4': '物质冲突'};

function quadMixHTML(events) {
    var counts = {'1': 0, '2': 0, '3': 0, '4': 0}, total = 0;
    events.forEach(e => { var q = String(e.quad_class || ''); if (q in counts) { counts[q]++; total++; } });
    if (!total) return '';
    var bar = '', legend = '';
    Object.keys(QUAD).forEach(function (q) {
        if (counts[q]) bar += '<span class="quad-' + q + '" style="flex-grow:' + counts[q] + '" title="' + QUAD[q] + ' ' + counts[q] + ' 条"></span>';
        legend += '<span><i class="quad-' + q + '"></i>' + QUAD[q] + ' ' + counts[q] + '</span>';
    });
    return '<div class="quad-mix" role="img" aria-label="按 CAMEO 四类的报道记录构成"><div class="quad-bar">' + bar
        + '</div><div class="quad-legend">' + legend + '</div></div>';
}
function isScore(v) { return typeof v === 'number' && Number.isFinite(v); }
function riskTone(score) { return score >= 70 ? 'high' : score >= 40 ? 'medium' : 'low'; }
var TONE_LABEL = {high: '高风险', medium: '中风险', low: '低风险'};

function daysBetween(a, b) {
    return Math.round((new Date(b + 'T00:00:00Z') - new Date(a + 'T00:00:00Z')) / 86400000);
}

/* 270° 仪表：底轨按 40/70 分三段着色；非截止日观测整体置灰，避免把旧值读成当前态势。 */
function situationGaugeHTML(latest, current, endDate) {
    var size = 200, c = size / 2, r = 80, circ = 2 * Math.PI * r, arc = circ * 0.75;
    var score = latest ? latest.risk_score : null;
    var pct = isScore(score) ? Math.max(0, Math.min(100, score)) / 100 : 0;
    var tone = isScore(score) ? riskTone(score) : 'none';
    var rot = 'rotate(135 ' + c + ' ' + c + ')';
    function seg(from, to, cls) {
        return '<circle class="sg-zone ' + cls + '" cx="' + c + '" cy="' + c + '" r="' + r + '" transform="' + rot
            + '" stroke-dasharray="' + (arc * (to - from)).toFixed(2) + ' ' + circ.toFixed(2)
            + '" stroke-dashoffset="' + (-arc * from).toFixed(2) + '"/>';
    }
    var svg = '<svg viewBox="0 0 ' + size + ' ' + size + '" aria-hidden="true">'
        + seg(0, 0.4, 'zone-low') + seg(0.4, 0.7, 'zone-medium') + seg(0.7, 1, 'zone-high');
    if (isScore(score)) {
        var angle = (135 + 270 * pct) * Math.PI / 180;
        svg += '<circle class="sg-fill" cx="' + c + '" cy="' + c + '" r="' + r + '" transform="' + rot
            + '" style="--dash:' + (arc * pct).toFixed(2) + 'px;--circ:' + circ.toFixed(2) + 'px"/>'
            + '<circle class="sg-dot" cx="' + (c + r * Math.cos(angle)).toFixed(2) + '" cy="' + (c + r * Math.sin(angle)).toFixed(2) + '" r="7"/>';
    }
    svg += '</svg>';
    var center;
    if (current) {
        center = '<div class="sg-value">' + escapeHtml(formatNumber(score, 1)) + '</div>'
            + '<div class="sg-level">' + TONE_LABEL[tone] + '</div>'
            + '<div class="sg-date">截止日 ' + escapeHtml(endDate) + '</div>';
    } else if (latest) {
        var age = daysBetween(latest.date, endDate);
        center = '<div class="sg-flag">无当前观测</div>'
            + '<div class="sg-value">' + escapeHtml(formatNumber(score, 1)) + '</div>'
            + '<div class="sg-date">最新 ' + escapeHtml(latest.date.slice(5)) + ' · ' + age + ' 天前</div>';
    } else {
        center = '<div class="sg-value">—</div><div class="sg-flag">无当前观测</div>';
    }
    var title = current ? '截止日风险 ' + formatNumber(score, 1) + '（0～100，非发生概率）'
        : latest ? '截止日无观测；最新观测 ' + latest.date + ' 为 ' + formatNumber(score, 1) + ' 分，已置灰' : '窗口内无有效风险观测';
    return '<div class="sit-gauge ' + (current ? 'is-current tone-' + tone : 'is-stale') + '" role="img" aria-label="'
        + escapeHtml(title) + '" title="' + escapeHtml(title) + '">' + svg
        + '<div class="sg-center">' + center + '</div><div class="sg-scale"><span>0</span><span>100</span></div></div>';
}

/* 每个自然日一格：柱高=风险分，颜色=等级；缺测为斜纹短格，绝不补零或沿用旧值。 */
function riskStripHTML(observed, endDate, days) {
    var byDate = {};
    observed.forEach(r => { byDate[r.date] = r.risk_score; });
    var end = new Date(endDate + 'T00:00:00Z'), cells = '', count = 0, start = '';
    for (var i = days - 1; i >= 0; i--) {
        var d = new Date(end.getTime());
        d.setUTCDate(end.getUTCDate() - i);
        var key = d.toISOString().slice(0, 10), v = byDate[key];
        if (!start) start = key;
        if (isScore(v)) {
            count++;
            cells += '<span class="rs-cell tone-' + riskTone(v) + '" style="--h:' + (14 + 86 * Math.max(0, Math.min(100, v)) / 100).toFixed(1)
                + '%" title="' + key + ' · ' + formatNumber(v, 1) + ' 分"></span>';
        } else {
            cells += '<span class="rs-cell rs-missing" title="' + key + ' · 缺测"></span>';
        }
    }
    return '<div class="risk-strip-wrap"><div class="strip-head"><span>逐日风险</span><span class="strip-legend">'
        + '<i class="tone-low"></i>低<i class="tone-medium"></i>中<i class="tone-high"></i>高<i class="rs-missing"></i>缺测</span></div>'
        + '<div class="risk-strip" role="img" aria-label="' + escapeHtml(days + '天窗口内有效观测' + count + '天，缺测' + (days - count) + '天') + '">' + cells + '</div>'
        + '<div class="strip-axis"><span>' + escapeHtml(start) + '</span><span>' + escapeHtml(endDate) + '</span></div></div>';
}

/* ================= 数据源健康 ================= */
async function loadSourceHealth() {
    var sequence = dashboardSequence;
    var el = document.getElementById('source-health-body');
    renderLoading(el);
    try {
        var json = await fetchJSON('/api/sources/health');
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
            var stance = s.stance || {};
            html += '<div class="sh-item">'
                + '<div class="sh-head"><span class="dot" style="background:' + meta.color + '"></span>'
                + '<span class="sh-name">' + escapeHtml(name) + '</span>'
                + (stance.label ? '<span class="sh-stance" title="' + escapeHtml(stance.note || '信源立场标注，仅展示不参与评分') + '">' + escapeHtml(stance.label) + '</span>' : '')
                + '<span class="sh-status" style="color:' + meta.color + '" title="状态按最近一次采集结果判定；历史成功率反映长期稳定性">' + meta.label + '</span></div>'
                + '<div class="sh-meta">历史成功率 ' + escapeHtml(formatNumber(s.success_rate * 100, 0)) + '% ('
                + escapeHtml(String(s.recent_success)) + '/' + escapeHtml(String(s.recent_attempts)) + ')'
                + ' · 上轮 ' + escapeHtml(String(s.last_count)) + ' 条</div>';
            var last = (s.records || []).slice(-1)[0];
            html += '<p class="sh-meta">最近运行：' + escapeHtml(last?.time || '未提供') + '；最近成功：' + escapeHtml(s.last_success || '尚无成功') + '</p>';
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
        var json = await fetchJSON('/api/geo_potential?' + dashboardQuery({local:1}));
        if (sequence !== dashboardSequence) return;
        hideLoading(geoEl); hideLoading(acEl);
        if (!json.success) { geoEl.innerHTML = acEl.innerHTML = errBox(json.error); return; }
        var d = json.data;

        // 位势 Top5
        var top = (d.potential && d.potential.provinces || []).slice(0, 6);
        var html = '<div class="muted-note">' + escapeHtml(d.potential.model || '') + '</div>';
        top.forEach(function (p) {
            html += metricBarHTML(
                p.province + ' (' + (p.dominant_center || '') + ')',
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
        var local = d.spatial_report || {};
        var localEl = document.getElementById('spatial-detail');
        if (localEl) {
            var report = '<p>' + escapeHtml(local.reason || '局部统计为描述性检验，不代表因果。') + '</p>';
            Object.entries(local.by_matrix || {}).forEach(([kind, r]) => { report += '<p>' + escapeHtml(kind) + '：I=' + escapeHtml(formatNumber(r.moran_i, 3)) + '；p=' + escapeHtml(formatNumber(r.p_value, 3)) + '；有效区域 ' + escapeHtml(r.sample_count) + '</p>'; });
            for (var key of ['lisa','gi_star']) {
                var result = local[key] || (key === 'gi_star' ? local.gistar : null) || {};
                report += '<h4>' + (key === 'lisa' ? 'LISA' : 'Getis-Ord Gi*') + '</h4><p>' + escapeHtml(result.reason || result.interpretation || '暂无可报告局部结果') + '</p>';
                (result.units || []).forEach(r => {
                    var meaning = key === 'lisa' ? ((r.cluster || '—') + (r.significant ? '（显著）' : '（不显著）')) : (r.hotspot ? '热点（近似z阈值）' : r.coldspot ? '冷点（近似z阈值）' : '未达热点／冷点阈值');
                    report += '<p>' + escapeHtml(r.region) + '：' + escapeHtml(meaning) + (key === 'lisa' ? '；p=' + escapeHtml(formatNumber(r.p_value,3)) : '；Gi*=' + escapeHtml(formatNumber(r.gi_star,3))) + '</p>';
                });
            }
            localEl.innerHTML = report;
        }
    } catch (e) {
        if (sequence !== dashboardSequence) return;
        hideLoading(geoEl); hideLoading(acEl);
        geoEl.innerHTML = acEl.innerHTML = errBox(e.message);
        var localEl = document.getElementById('spatial-detail');
        if (localEl) localEl.innerHTML = errBox(e.message);
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
                html += metricBarHTML(a.name + ' [' + (a.role || '') + ']', a.degree_centrality, 1);
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
                : fo.projected_level === 'yellow' ? '#d9a93a' : 'var(--text-muted)';
            // 末值基线不是外推：回测不足时后端直接沿用最后一个观测值，标题必须如实区分
            var isBaseline = /末值基线/.test(fo.reliability || '') || fo.model === 'last';
            html += '<div class="sub-title" style="margin-top:0.6rem">未来 ' + escapeHtml(String(fo.days_ahead || 7))
                + (isBaseline ? ' 天（末值基线，未做外推）' : ' 天探索性外推') + '</div>';
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
        axisLine: { lineStyle: { color: '#d6e0dc' } },
        axisLabel: { color: '#576873', fontSize: 10 },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#edf2f0', type: 'dashed' } }
    };
    var muted = { color: '#576873' };
    mmChart.setOption({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.96)', borderColor: '#e2e9e6', textStyle: { color: '#24333b' },
            extraCssText: 'border-radius:12px;box-shadow:0 12px 28px -14px rgba(36,51,59,0.35);' },
        legend: { data: ['夜光原值', '冲突类记录数', '情感风险'], top: 0, icon: 'roundRect', itemWidth: 14, itemHeight: 6, textStyle: { color: '#576873' } },
        grid: [
            { left: 65, right: 20, top: '16%', height: '18%' },
            { left: 65, right: 20, top: '46%', height: '18%' },
            { left: 65, right: 20, top: '76%', height: '16%' }
        ],
        xAxis: [0, 1, 2].map(function (i) { return Object.assign({}, axisStyle, { type: 'category', data: months, gridIndex: i, axisLabel: { show: i === 2, color: '#576873' } }); }),
        yAxis: [
            Object.assign({ gridIndex: 0, type: 'value', name: '夜光原值', nameTextStyle: muted }, axisStyle),
            Object.assign({ gridIndex: 1, type: 'value', name: '冲突类记录数', minInterval: 1, nameTextStyle: muted }, axisStyle),
            Object.assign({ gridIndex: 2, type: 'value', name: '情感0～1', min: 0, max: 1, nameTextStyle: muted }, axisStyle)
        ],
        series: [
            { name: '夜光原值', type: 'line', xAxisIndex: 0, yAxisIndex: 0, connectNulls: false, data: nightlight, symbolSize: 6,
              itemStyle: { color: '#e0a64a' }, lineStyle: { width: 2.2, color: '#e0a64a' } },
            { name: '冲突类记录数', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: conflict, barMaxWidth: 26,
              itemStyle: { color: 'rgba(219,102,94,0.55)', borderRadius: [6, 6, 0, 0] } },
            { name: '情感风险', type: 'line', xAxisIndex: 2, yAxisIndex: 2, connectNulls: false, data: sentiment, symbolSize: 6,
              itemStyle: { color: '#6c93d6' }, lineStyle: { width: 2.2, color: '#6c93d6' } }
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
    var missing = value == null || /无数据|无当前观测|N\/A|^—$/.test(String(value));
    return '<div class="stat-chip' + (missing ? ' stat-missing' : '') + '"><div class="sc-value">' + escapeHtml(String(value != null ? value : '—')) + '</div><div class="sc-label">' + escapeHtml(label) + '</div></div>';
}
function corrChip(label, value) {
    if (value === null || value === undefined || !Number.isFinite(value)) {
        return '<div class="corr-chip"><div class="cc-value">不可报告</div><div class="cc-label">' + escapeHtml(label) + '</div></div>';
    }
    var color = Math.abs(value) > 0.5 ? 'var(--risk-high)' : Math.abs(value) > 0.3 ? 'var(--risk-medium)' : 'var(--text-muted)';
    return '<div class="corr-chip"><div class="cc-value" style="color:' + color + '">' + escapeHtml(formatNumber(value, 2)) + '</div><div class="cc-label">' + escapeHtml(label) + '</div></div>';
}
