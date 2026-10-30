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
    updateFilterSummary();
    loadTrend();
});

/* ---------- 预警状态指示灯 ---------- */
async function loadAlertStatus(filters, sequence) {
    var indicator = document.getElementById('alert-indicator');
    if (!indicator) return;
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
        + 'background:rgba(255,255,255,0.62);border-radius:16px;backdrop-filter:blur(2px);';
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
    var indicator = document.getElementById('alert-indicator');
    if (indicator) indicator.textContent = '预警状态待更新';
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

/* ---------- ECharts 图表（浅色「晨雾」配色） ---------- */
var TREND_SERIES_COLORS = {
    '风险分': '#3a9a8a', '移动平均': '#9787cc', '预测': '#dd973b',
    '经验区间下界': '#e9bd84', '经验区间上界': '#e9bd84'
};

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

    var axisStyle = {
        axisLine: { lineStyle: { color: '#d6e0dc' } },
        axisLabel: { color: '#576873', fontSize: 11 },
        splitLine: { lineStyle: { color: '#edf2f0', type: 'dashed' } },
        axisTick: { show: false }
    };

    var option = {
        backgroundColor: 'transparent',
        textStyle: { fontFamily: '"PingFang SC", "Microsoft YaHei", sans-serif' },
        title: {
            text: chartData.title || '风险趋势',
            left: 8,
            top: 4,
            textStyle: { color: '#24333b', fontSize: 15, fontWeight: 600, fontFamily: '"Noto Serif SC", "Songti SC", serif' }
        },
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(255,255,255,0.96)',
            borderColor: '#e2e9e6',
            borderWidth: 1,
            padding: [8, 12],
            textStyle: { color: '#24333b', fontSize: 12 },
            extraCssText: 'border-radius:12px;box-shadow:0 12px 28px -14px rgba(36,51,59,0.35);',
            // 缺测统一用全角破折号，与图谱/位势页的占位符风格一致（ECharts 默认是半角 -）
            formatter: function (params) {
                if (!params || !params.length) return '';
                var lines = [params[0].axisValueLabel || params[0].name || ''];
                params.forEach(function (p) {
                    var v = p.value;
                    var text = (v === null || v === undefined || v === '' || (typeof v === 'number' && isNaN(v)))
                        ? '—' : v;
                    lines.push((p.marker || '') + ' ' + (p.seriesName || '') + ' ' + text);
                });
                return lines.join('<br/>');
            }
        },
        legend: {
            data: (chartData.series || []).map(function (s) { return s.name; }),
            top: 6,
            right: 8,
            icon: 'roundRect',
            itemWidth: 14,
            itemHeight: 6,
            textStyle: { color: '#576873' }
        },
        grid: {
            left: '4%', right: '3%', top: 56, bottom: '3%', containLabel: true
        },
        xAxis: Object.assign({}, axisStyle, {
            type: 'category',
            data: chartData.xAxis,
            axisLabel: { rotate: 40, color: '#576873', fontSize: 10,
                formatter: function (v) { return /^\d{4}-\d{2}-\d{2}$/.test(v) ? v.slice(5) : v; } }
        }),
        yAxis: Object.assign({
            type: 'value',
            name: (chartData.yAxis && chartData.yAxis.name) || '风险分',
            // 轴名默认居中于轴线，左侧会被画布裁掉首字；改为左对齐并留出间距
            nameTextStyle: { color: '#576873', align: 'left', padding: [0, 0, 6, 0] },
            nameGap: 12,
            min: 0, max: 100
        }, axisStyle),
        series: (chartData.series || []).map(function (s, index) {
            var item = {
                name: s.name,
                type: s.type,
                data: s.data,
                smooth: s.smooth,
                connectNulls: false
            };
            var color = TREND_SERIES_COLORS[s.name] || ['#3a9a8a', '#6c93d6', '#9787cc', '#dd973b'][index % 4];
            if (s.type === 'line') {
                item.itemStyle = { color: color };
                item.lineStyle = { width: 2.2, color: color };
                item.symbol = 'circle';
                item.symbolSize = 5;
                // 主线：玉青柔光 + 渐隐填充；其余参考线不填充，避免画面发闷
                if (index === 0) {
                    item.lineStyle.shadowBlur = 10;
                    item.lineStyle.shadowColor = 'rgba(58,154,138,0.25)';
                    item.areaStyle = {
                        color: {
                            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                            colorStops: [
                                { offset: 0, color: 'rgba(58,154,138,0.24)' },
                                { offset: 1, color: 'rgba(108,147,214,0.02)' }
                            ]
                        }
                    };
                }
            }
            if (s.lineStyle) item.lineStyle = Object.assign(item.lineStyle || {}, s.lineStyle);
            if (s.areaStyle) item.areaStyle = Object.assign(item.areaStyle || {}, s.areaStyle);
            if (index === 0 && item.areaStyle && item.areaStyle.color) item.areaStyle.opacity = 1;
            if (s.itemStyle) item.itemStyle = Object.assign(item.itemStyle || {}, s.itemStyle);
            if (s.markLine) item.markLine = s.markLine;
            if (s.markPoint) item.markPoint = s.markPoint;
            if (s.symbol) item.symbol = s.symbol;
            if (s.symbolSize) item.symbolSize = s.symbolSize;
            return item;
        })
    };

    var main = option.series[0];
    if (main && main.markLine) {
        // 预警阈值：细虚线 + 末端标签，不画箭头端点。
        // 每条线沿用服务端预警色，标签同色，避免细点线被渲染成灰色。
        var lines = (main.markLine.data || []).map(function (d) {
            var c = (d.lineStyle && d.lineStyle.color) || '#93a2aa';
            return Object.assign({}, d, {
                lineStyle: { color: c, type: 'dashed', width: 1.4, opacity: 0.9 },
                label: { color: c, fontWeight: 600 }
            });
        });
        main.markLine = Object.assign({}, main.markLine, { symbol: 'none', data: lines,
            label: { position: 'insideEndTop', fontSize: 10,
                formatter: function (p) { return (p.name || '') + ' ' + p.value; } } });
    }
    var gaps = missingRuns(chartData);
    if (main && gaps.length) {
        main.markArea = { silent: true, itemStyle: { color: 'rgba(148,163,170,0.11)' },
            label: { position: 'insideTop', color: '#7d8c93', fontSize: 11 }, data: gaps };
    }
    trendChart.setOption(option, {notMerge: true});
    trendChart.resize();
}

/* 连续缺测日着浅灰底并标注天数；预测延伸段不算缺测。 */
function missingRuns(chartData) {
    var x = chartData.xAxis || [], series = chartData.series || [];
    var actual = (series[0] || {}).data || [];
    var forecast = series.find(function (s) { return s.name === '预测'; });
    var runs = [], start = -1;
    for (var i = 0; i <= x.length; i++) {
        var missing = i < x.length && actual[i] == null && !(forecast && forecast.data && forecast.data[i] != null);
        if (missing && start < 0) start = i;
        if (!missing && start >= 0) {
            var n = i - start;
            if (n >= 2) runs.push([{ xAxis: x[start], name: '缺测 ' + n + ' 天' }, { xAxis: x[i - 1] }]);
            start = -1;
        }
    }
    return runs;
}
