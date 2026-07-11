/**
 * 对话分析页面 JS
 * 依赖: common.js (escapeHtml, fetchJSON, showToast, gaugeRingHTML, metricBarHTML 等)
 */

/* ---------- 示例文本 ---------- */
const SAMPLE_TEXT = '缅甸军方与克钦独立军在掸邦北部发生武装冲突\n\n' +
    '据缅华网报道，2026年6月14日凌晨，缅甸军方与克钦独立军在掸邦北部抹谷镇附近发生激烈交火。' +
    '冲突持续约4小时，造成双方多人伤亡。当地居民称，军方出动了空中力量进行轰炸，导致多个村庄的平民被迫转移。\n\n' +
    '克钦独立军发言人表示，此次冲突是由于军方违反停火协议，对克钦独立军控制区发动进攻所致。' +
    '缅甸军方尚未对此事发表正式声明。\n\n' +
    '分析人士指出，此次冲突可能会影响中缅经济走廊相关项目的推进，并对边境地区的安全形势产生负面影响。' +
    '联合国难民署表示，已有约2000名平民逃离冲突区域，涌向中国边境方向。';

/* ---------- 初始化 ---------- */
document.addEventListener('DOMContentLoaded', function () {
    var input = document.getElementById('news-input');
    if (input) {
        input.addEventListener('input', updateCharCount);
    }
});

function fillSampleText() {
    document.getElementById('news-input').value = SAMPLE_TEXT;
    updateCharCount();
}

function updateCharCount() {
    var input = document.getElementById('news-input');
    var counter = document.getElementById('char-count');
    if (!input || !counter) return;
    var len = input.value.length;
    counter.textContent = len + ' / 10000';
    counter.className = 'char-count' + (len > 9000 ? ' over' : len > 7000 ? ' warn' : '');
}

/* ---------- 分析 ---------- */
async function analyzeText() {
    var input = document.getElementById('news-input');
    var text = input ? input.value.trim() : '';
    if (!text) {
        showToast('请输入新闻文本', 'error');
        return;
    }
    if (text.length > 10000) {
        showToast('文本超过 10000 字符限制', 'error');
        return;
    }

    var btn = document.getElementById('analyze-btn');
    var resultArea = document.getElementById('result-area');
    btn.disabled = true;
    btn.textContent = '分析中...';
    resultArea.style.display = 'none';

    try {
        var json = await fetchJSON('/api/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text })
        });

        if (json.success) {
            displayResults(json.data);
            resultArea.style.display = 'block';
        } else {
            showToast('分析失败: ' + (json.error || '未知错误'), 'error');
        }
    } catch (e) {
        showToast('请求失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '开始分析';
    }
}

/* ---------- 结果渲染 ---------- */
function displayResults(data) {
    renderEntitiesCard(data.entities);
    renderSentimentCard(data.sentiment);
    renderRiskCard(data.risk_score);
    renderLlmCard(data.llm_analysis);
    renderGdeltCard(data.gdelt_metrics);
}

/* -- 实体识别 -- */
function renderEntitiesCard(entities) {
    var el = document.getElementById('entities-body');
    if (!el) return;
    var ent = entities || {};
    var html = '';

    var groups = [
        { key: 'locations', label: '地名', cls: 'location' },
        { key: 'organizations', label: '组织', cls: 'organization' },
        { key: 'persons', label: '人物', cls: 'person' }
    ];

    groups.forEach(function (g) {
        var arr = ent[g.key] || [];
        html += '<div style="margin-bottom:0.5rem"><span style="color:var(--text-muted);font-size:0.8rem">'
            + escapeHtml(g.label) + '</span><br>';
        if (arr.length === 0) {
            html += '<span style="color:var(--text-muted)">无</span>';
        } else {
            arr.forEach(function (item) {
                html += '<span class="entity-tag ' + g.cls + '">' + escapeHtml(item) + '</span> ';
            });
        }
        html += '</div>';
    });
    el.innerHTML = html;
}

/* -- 情感分析 -- */
function renderSentimentCard(sentiment) {
    var el = document.getElementById('sentiment-body');
    if (!el) return;
    var s = sentiment || {};
    var riskCls = riskLevelClass(s.risk_level);

    var html = gaugeRingHTML((s.risk_score || 0) * 100, 100, '风险值');
    html += '<div style="text-align:center;margin-top:0.75rem">';
    html += '<span class="risk-badge ' + riskCls + '">' + escapeHtml(s.risk_level || 'N/A') + '</span>';
    html += '</div>';
    html += '<div style="margin-top:0.75rem">';
    html += metricBarHTML('情感分', s.sentiment_score || 0, 1, 'var(--accent-blue)');
    html += '</div>';
    el.innerHTML = html;
}

/* -- 风险评分 -- */
function renderRiskCard(risk) {
    var el = document.getElementById('risk-body');
    if (!el) return;
    var r = risk || {};
    var score = r.risk_score || 0;
    var riskCls = riskLevelClass(r.risk_level);

    var html = gaugeRingHTML(score, 130, '综合风险');
    html += '<div style="text-align:center;margin-top:0.75rem">';
    html += '<span class="risk-badge ' + riskCls + '">' + escapeHtml(r.risk_level || 'N/A') + '</span>';
    html += '</div>';

    // 权重归一化提示
    if (r.weight_rebalanced) {
        html += '<div class="rebalance-note">⚡ 权重已动态归一化（部分指标暂缺）</div>';
    }

    // 指标明细
    var indicators = r.indicator_scores;
    if (indicators && typeof indicators === 'object') {
        html += '<div style="margin-top:0.75rem">';
        var nameMap = {
            'conflict_frequency': '冲突频次',
            'sentiment_avg': '情感风险',
            'nightlight_change': '夜光变化',
            'refugee_change': '难民变化',
            'event_severity': '事件严重度'
        };
        Object.keys(indicators).forEach(function (k) {
            var label = nameMap[k] || k;
            var val = indicators[k];
            html += metricBarHTML(label, val, 1);
        });
        html += '</div>';
    }

    if (r.gdelt_used) {
        html += '<div class="info-banner" style="margin-top:0.5rem">📡 GDELT 数据已融合</div>';
    }
    el.innerHTML = html;
}

/* -- LLM 分析 -- */
function renderLlmCard(llm) {
    var el = document.getElementById('llm-body');
    if (!el) return;
    var l = llm || {};

    if (l.error) {
        el.innerHTML = '<div class="error-card"><div class="error-icon">⚠️</div>'
            + '<div class="error-msg">' + escapeHtml(l.error) + '</div></div>';
        return;
    }

    var fields = [
        { key: 'event_type', label: '事件类型' },
        { key: 'summary', label: '事件摘要' },
        { key: 'china_myanmar_impact', label: '中缅影响' },
        { key: 'risk_warning', label: '风险提示' }
    ];
    var html = '';
    fields.forEach(function (f) {
        if (l[f.key]) {
            html += '<div class="llm-field">'
                + '<div class="lf-label">' + escapeHtml(f.label) + '</div>'
                + '<div class="lf-value">' + escapeHtml(l[f.key]) + '</div></div>';
        }
    });
    if (!html) html = '<span style="color:var(--text-muted)">暂无 LLM 分析结果</span>';
    el.innerHTML = html;
}

/* -- GDELT 指标 -- */
function renderGdeltCard(gdelt) {
    var el = document.getElementById('gdelt-body');
    if (!el) return;
    var g = gdelt || {};
    if (!g.article_count) {
        el.innerHTML = '<div class="empty-state"><div class="empty-icon">📡</div><p>暂无 GDELT 数据</p></div>';
        return;
    }
    var html = '<div class="gdelt-grid">';
    html += gdeltCell('文章数', g.article_count);
    html += gdeltCell('冲突数', g.conflict_count);
    html += gdeltCell('冲突频率', formatPercent(g.conflict_frequency));
    html += gdeltCell('平均风险', formatNumber(g.avg_tone_risk));
    html += gdeltCell('平均严重度', formatNumber(g.avg_severity));
    html += gdeltCell('最高严重度', formatNumber(g.max_severity));
    html += '</div>';

    // 事件摘要
    var summary = g.event_summary;
    if (summary && typeof summary === 'object') {
        html += '<div style="margin-top:0.6rem;font-size:0.8rem;color:var(--text-secondary)">事件分布: ';
        Object.keys(summary).forEach(function (k) {
            html += '<span class="entity-tag event">' + escapeHtml(k) + ': ' + escapeHtml(String(summary[k])) + '</span> ';
        });
        html += '</div>';
    }
    el.innerHTML = html;
}

function gdeltCell(label, value) {
    return '<div class="gdelt-item"><div class="gi-value">' + escapeHtml(String(value)) + '</div>'
        + '<div class="gi-label">' + escapeHtml(label) + '</div></div>';
}
