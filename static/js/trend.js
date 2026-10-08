/**
 * 趋势预测页面 JS
 * 依赖: common.js + ECharts (static/vendor/echarts)
 */

var trendChart = null;
var trendRequestSequence = 0;
var trendResizeBound = false;

/* ---------- 初始化 ---------- */
document.addEventListener('DOMContentLoaded', function () {
    initViewFilters(loadTrend);
    var domain = document.getElementById('domain-filter');
    domain.value = new URLSearchParams(window.location.search).get('domain') || 'observed';
    domain.addEventListener('change', loadTrend);
    loadTrend();
});

/* ---------- 预警状态指示灯 ---------- */
async function loadAlertStatus(filters, sequence) {
    var indicator = document.getElementById('alert-indicator');
    if (filters.domain === 'legacy' || filters.source) {
        if (indicator) indicator.textContent = '当前筛选不适用正式预警';
        return;
    }
    try {
        var json = await fetchJSON('/api/alert?' + viewQuery({}, filters));
        if (sequence !== trendRequestSequence) return;
        if (json.success && json.data.status) {
            var s = json.data.status;
            var el = document.getElementById('alert-indicator');
            if (el) {
                el.innerHTML = '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;'
                    + 'background:' + escapeHtml(s.color) + ';margin-right:6px;"></span>'
                    + '<span style="color:' + escapeHtml(s.color) + ';font-size:0.85rem;">'
                    + escapeHtml(s.label) + '</span>';
            }
        }
    } catch (e) {
        if (sequence === trendRequestSequence && indicator) indicator.textContent = '预警状态获取失败';
    }
}

/* ---------- 加载态 ---------- */
/* 注意：图表容器内是 ECharts 自己的渲染根，绝不能用 renderLoading 的
   innerHTML='' 清空它——否则实例缓存后 setOption 会画到游离 DOM 上，
   页面永久空白。改用遮罩层覆盖。 */
function showChartLoading(el) {
    if (!el) return;
    hideChartLoading(el);
    el.style.position = 'relative';
    var mask = createEl('div', 'chart-loading-mask');
    mask.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;'
        + 'z-index:10;display:flex;align-items:center;justify-content:center;'
        + 'background:rgba(13,17,23,0.55);border-radius:8px;';
    var radar = createEl('div', 'loader-radar');
    radar.appendChild(createEl('div', 'sweep'));
    radar.appendChild(createEl('div', 'core'));
    mask.appendChild(radar);
    el.appendChild(mask);
}

function hideChartLoading(el) {
    if (!el) return;
    var mask = el.querySelector('.chart-loading-mask');
    if (mask) mask.remove();
}

/* ---------- 加载趋势数据 ---------- */
async function loadTrend() {
    var sequence = ++trendRequestSequence;
    var days = document.getElementById('days-select').value;
    var summaryEl = document.getElementById('summary-cards');
    var chartEl = document.getElementById('trend-chart');

    renderLoading(summaryEl);
    showChartLoading(chartEl);
    syncReportLinks(null);
    document.getElementById('alert-indicator').textContent = '预警状态待更新';
    var params = new URLSearchParams({days: days, chart: 'true', domain: document.getElementById('domain-filter').value});
    ['end_date', 'region', 'source'].forEach(function (key) {
        var el = document.getElementById(key + '-filter');
        if (el && el.value.trim()) params.set(key, el.value.trim());
    });

    try {
        var json = await fetchJSON('/api/trend?' + params.toString());
        if (sequence !== trendRequestSequence) return;
        hideChartLoading(chartEl);
        if (json.success) {
            var data = json.data;
            hideLoading(summaryEl);
            renderSummary(data);
            renderForecast(data);
            renderAnomalies(data.anomalies);
            renderChart(data.chart_data);
            syncReportLinks(data);
            syncViewNavigation(data.filters);
            loadAlertStatus(data.filters || {}, sequence);
            var shown = new URLSearchParams();
            Object.keys(data.filters || {}).forEach(function (key) {
                if (data.filters[key] != null) shown.set(key, data.filters[key]);
            });
            window.history.replaceState(null, '', window.location.pathname + '?' + shown.toString());
        } else {
            hideLoading(summaryEl);
            renderChart(null);
            summaryEl.textContent = '加载失败，当前窗口数据不可用';
            renderForecast({});
            renderAnomalies([]);
            showToast('加载失败: ' + (json.error || ''), 'error');
        }
    } catch (e) {
        if (sequence !== trendRequestSequence) return;
        renderChart(null);
        summaryEl.textContent = '请求失败，当前窗口数据不可用';
        renderForecast({});
        renderAnomalies([]);
        hideChartLoading(chartEl);
        hideLoading(summaryEl);
        showToast('请求失败: ' + e.message, 'error');
    }
}

/* 报告导出链接跟随当前时间窗口 */
function syncReportLinks(data) {
    setViewReport(data);
}

/* ---------- 摘要卡片 ---------- */
function renderSummary(data) {
    var el = document.getElementById('summary-cards');
    if (!el) return;
    var t = data.trend_analysis || {};
    var score = t.latest_score;
    var meta = data.metadata || {};
    var trendClass = 'trend-' + (t.trend || '').toLowerCase();
    // 中文趋势名映射
    var trendLabel = t.trend || '无数据';
    if (trendLabel === '上升' || trendLabel === 'up') trendClass = 'trend-up';
    else if (trendLabel === '下降' || trendLabel === 'down') trendClass = 'trend-down';
    else trendClass = 'trend-stable';

    var html = '';
    html += summaryCard('当前趋势', '<span class="' + trendClass + '">' + escapeHtml(trendLabel) + '</span>');
    html += summaryCard('最新风险分', escapeHtml(formatNumber(score, 1)));
    html += summaryCard('平均分', escapeHtml(formatNumber(t.avg_score, 1)));
    html += summaryCard('有效日 / 覆盖率', escapeHtml(String(t.data_points || 0) + ' / ' + formatNumber((meta.coverage || 0) * 100, 1) + '%'));
    html += summaryCard('最新观测', escapeHtml(meta.latest_observation || '无观测'));
    html += summaryCard('实际来源', escapeHtml((meta.sources || []).join('、') || '无来源'));
    if (meta.stale) html += '<p class="empty-state">截止日缺测，最新观测不代表当前风险。</p>';
    (data.warnings || []).forEach(function (warning) {
        html += '<p class="empty-state">' + escapeHtml(warning) + '</p>';
    });
    el.innerHTML = html;
}

function summaryCard(label, valueHTML) {
    return '<div class="summary-card">'
        + '<div class="card-label">' + escapeHtml(label) + '</div>'
        + '<div class="card-value">' + valueHTML + '</div></div>';
}

/* ---------- 预测信息 ---------- */
function renderForecast(data) {
    var el = document.getElementById('forecast-area');
    if (!el) return;
    var meta = data.forecast_meta || {};
    var forecast = data.forecast || [];

    if (!forecast.length) {
        el.innerHTML = '<div class="empty-state"><p>' + escapeHtml(meta.reason || '暂无预测数据') + '</p></div>';
        return;
    }

    var html = '<div class="forecast-grid">';
    html += forecastCell('7日预测值', forecast.length > 0 ? formatNumber(forecast[forecast.length - 1], 1) : 'N/A');
    html += forecastCell('斜率', formatNumber(meta.slope, 4));
    // 置信度是文字等级（高/中/低），不能走 formatNumber（会变 NaN）
    html += forecastCell('预测状态', meta.reason || '探索性预测，尚未验收');
    html += forecastCell('拟合优度 R²（非置信度）', formatNumber(meta.r_squared, 4));
    if (meta.backtest && meta.backtest.models && meta.backtest.models[meta.backtest.selected_model]) {
        var selected = meta.backtest.models[meta.backtest.selected_model];
        html += forecastCell('回测 MAE / RMSE', formatNumber(selected.mae, 2) + ' / ' + formatNumber(selected.rmse, 2));
    }
    if (meta.interval) html += forecastCell('经验区间', meta.interval.label);
    html += '</div>';

    // 预测序列
    if (forecast.length > 0) {
        html += '<div style="margin-top:0.6rem;font-size:0.8rem;color:var(--text-secondary)">';
        html += '未来7天预测: ' + forecast.map(function (v) { return formatNumber(v, 1); }).join(' → ');
        html += '</div>';
    }
    el.innerHTML = html;
}

function forecastCell(label, value) {
    return '<div class="forecast-item"><div class="fi-label">' + escapeHtml(label) + '</div>'
        + '<div class="fi-value">' + escapeHtml(String(value)) + '</div></div>';
}

/* ---------- 异常事件 ---------- */
function renderAnomalies(anomalies) {
    var el = document.getElementById('anomaly-area');
    if (!el) return;
    if (!anomalies || !anomalies.length) {
        el.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem">暂无可确认异常（也可能是有效历史不足）</div>';
        return;
    }

    var html = '<div class="anomaly-list">';
    anomalies.forEach(function (a) {
        html += '<div class="anomaly-item">'
            + '<span class="anomaly-date">' + escapeHtml(a.date || '') + '</span>'
            + '<span class="anomaly-score">' + escapeHtml(formatNumber(a.score != null ? a.score : a.value, 1)) + '</span>'
            + '<span class="anomaly-deviation">偏差 ' + escapeHtml(formatNumber(a.deviation != null ? a.deviation : a.z_score, 2)) + '</span>'
            + '</div>';
    });
    html += '</div>';
    el.innerHTML = html;
}

/* ---------- ECharts 图表 (暗色主题) ---------- */
function renderChart(chartData) {
    var chartDom = document.getElementById('trend-chart');
    if (!chartDom) return;
    var empty = chartDom.querySelector('.chart-empty-state');
    if (empty) empty.remove();
    if (!chartData || !(chartData.series || []).some(function (s) { return (s.data || []).some(function (v) { return v != null; }); })) {
        if (trendChart) trendChart.clear();
        var message = createEl('div', 'chart-empty-state');
        message.textContent = '此日历窗口无有效观测；不会用旧数据或零值补齐。';
        message.style.cssText = 'position:absolute;top:45%;width:100%;text-align:center;z-index:2;';
        chartDom.appendChild(message);
        return;
    }

    if (!trendChart) {
        trendChart = echarts.init(chartDom);
        // 窗口大小变化时自适应（只绑一次，重建实例时不叠加监听）
        if (!trendResizeBound) {
            window.addEventListener('resize', function () { trendChart && trendChart.resize(); });
            trendResizeBound = true;
        }
    } else if (!chartDom.querySelector('canvas,svg')) {
        // 防御：容器曾被清空（渲染根游离），销毁残留实例并重建
        try { trendChart.dispose(); } catch (e) { /* 容器已毁，忽略 */ }
        trendChart = echarts.init(chartDom);
    }

    // 暗色主题配置
    var darkAxisStyle = {
        axisLine: { lineStyle: { color: '#2a2d35' } },
        axisLabel: { color: '#8a8f98', fontSize: 11 },
        splitLine: { lineStyle: { color: '#1e2128', type: 'dashed' } },
        axisTick: { lineStyle: { color: '#2a2d35' } }
    };

    var option = {
        backgroundColor: 'transparent',
        title: {
            text: chartData.title || '风险趋势',
            left: 'center',
            textStyle: { color: '#f0f0f0', fontSize: 15, fontFamily: '"Noto Serif SC", serif' }
        },
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(26,29,35,0.95)',
            borderColor: '#2a2d35',
            textStyle: { color: '#e0e0e0', fontSize: 12 }
        },
        legend: {
            data: (chartData.series || []).map(function (s) { return s.name; }),
            top: 30,
            textStyle: { color: '#8a8f98' }
        },
        grid: {
            left: '3%', right: '4%', bottom: '3%', containLabel: true
        },
        xAxis: Object.assign({}, darkAxisStyle, {
            type: 'category',
            data: chartData.xAxis,
            axisLabel: { rotate: 45, color: '#8a8f98', fontSize: 10 }
        }),
        yAxis: Object.assign({
            type: 'value',
            name: (chartData.yAxis && chartData.yAxis.name) || '风险分',
            nameTextStyle: { color: '#8a8f98' },
            min: 0, max: 100
        }, darkAxisStyle),
        series: (chartData.series || []).map(function (s) {
            var item = {
                name: s.name,
                type: s.type,
                data: s.data,
                smooth: s.smooth,
                connectNulls: false
            };
            // 主线发光效果
            if (s.type === 'line') {
                item.lineStyle = {
                    width: 2,
                    shadowBlur: 8,
                    shadowColor: 'rgba(30,144,255,0.3)'
                };
                item.itemStyle = { color: '#1e90ff' };
                // 渐变填充
                item.areaStyle = {
                    color: {
                        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: 'rgba(30,144,255,0.25)' },
                            { offset: 1, color: 'rgba(30,144,255,0.02)' }
                        ]
                    }
                };
                item.symbol = 'circle';
                item.symbolSize = 4;
            }
            if (s.lineStyle) item.lineStyle = Object.assign(item.lineStyle || {}, s.lineStyle);
            if (s.areaStyle) item.areaStyle = Object.assign(item.areaStyle || {}, s.areaStyle);
            if (s.itemStyle) item.itemStyle = Object.assign(item.itemStyle || {}, s.itemStyle);
            if (s.markLine) item.markLine = s.markLine;
            if (s.markPoint) item.markPoint = s.markPoint;
            if (s.symbol) item.symbol = s.symbol;
            if (s.symbolSize) item.symbolSize = s.symbolSize;
            return item;
        })
    };

    trendChart.setOption(option, {notMerge: true});
    trendChart.resize();
}
