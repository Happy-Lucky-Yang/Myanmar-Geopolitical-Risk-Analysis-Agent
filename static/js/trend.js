/**
 * 趋势预测页面 JS
 * 依赖: common.js + ECharts CDN
 */

var trendChart = null;

/* ---------- 初始化 ---------- */
document.addEventListener('DOMContentLoaded', function () {
    loadTrend();
    loadAlertStatus();
});

/* ---------- 预警状态指示灯 ---------- */
async function loadAlertStatus() {
    try {
        var json = await fetchJSON('/api/alert');
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
    } catch (e) { /* silent */ }
}

/* ---------- 加载趋势数据 ---------- */
async function loadTrend() {
    var days = document.getElementById('days-select').value;
    var summaryEl = document.getElementById('summary-cards');
    var chartEl = document.getElementById('trend-chart');

    renderLoading(summaryEl);
    renderLoading(chartEl);

    try {
        var json = await fetchJSON('/api/trend?days=' + encodeURIComponent(days) + '&chart=true');
        if (json.success) {
            var data = json.data;
            hideLoading(summaryEl);
            hideLoading(chartEl);
            renderSummary(data);
            renderForecast(data);
            renderAnomalies(data.anomalies);
            renderChart(data.chart_data);
        } else {
            hideLoading(summaryEl);
            hideLoading(chartEl);
            showToast('加载失败: ' + (json.error || ''), 'error');
        }
    } catch (e) {
        hideLoading(summaryEl);
        hideLoading(chartEl);
        showToast('请求失败: ' + e.message, 'error');
    }
}

/* ---------- 摘要卡片 ---------- */
function renderSummary(data) {
    var el = document.getElementById('summary-cards');
    if (!el) return;
    var t = data.trend_analysis || {};
    var score = t.latest_score || 0;
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
    html += summaryCard('数据点数', escapeHtml(String(t.data_points || 0)));
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

    if (!forecast.length && !meta.slope) {
        el.innerHTML = '<div class="empty-state"><p>暂无预测数据</p></div>';
        return;
    }

    var html = '<div class="forecast-grid">';
    html += forecastCell('7日预测值', forecast.length > 0 ? formatNumber(forecast[forecast.length - 1], 1) : 'N/A');
    html += forecastCell('斜率', formatNumber(meta.slope, 4));
    html += forecastCell('置信度', formatNumber(meta.confidence, 2));
    html += forecastCell('R²', formatNumber(meta.r_squared, 4));
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
        el.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem">未检测到异常波动</div>';
        return;
    }

    var html = '<div class="anomaly-list">';
    anomalies.forEach(function (a) {
        html += '<div class="anomaly-item">'
            + '<span class="anomaly-date">' + escapeHtml(a.date || '') + '</span>'
            + '<span class="anomaly-score">' + escapeHtml(formatNumber(a.score || a.value, 1)) + '</span>'
            + '<span class="anomaly-deviation">偏差 ' + escapeHtml(formatNumber(a.deviation || a.z_score, 2)) + '</span>'
            + '</div>';
    });
    html += '</div>';
    el.innerHTML = html;
}

/* ---------- ECharts 图表 (暗色主题) ---------- */
function renderChart(chartData) {
    if (!chartData) return;
    var chartDom = document.getElementById('trend-chart');
    if (!chartDom) return;

    if (!trendChart) {
        trendChart = echarts.init(chartDom);
        // 窗口大小变化时自适应
        window.addEventListener('resize', function () { trendChart.resize(); });
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
        xAxis: Object.assign({
            type: 'category',
            data: chartData.xAxis,
            axisLabel: { rotate: 45, color: '#8a8f98', fontSize: 10 }
        }, darkAxisStyle),
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
                smooth: s.smooth
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

    trendChart.setOption(option);
}
