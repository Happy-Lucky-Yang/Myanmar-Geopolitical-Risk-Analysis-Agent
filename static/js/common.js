/**
 * 缅甸地缘风险智能分析系统 - 公共工具库
 * 提供 XSS 防护、通用 DOM 操作、API 请求封装、Toast 通知等
 */

/* ---------- XSS 防护 ---------- */

/**
 * 转义 HTML 特殊字符，防止 XSS
 * @param {*} str - 待转义的值（非字符串会先转为字符串）
 * @returns {string}
 */
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    const s = String(str);
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return s.replace(/[&<>"']/g, c => map[c]);
}

/* ---------- DOM 辅助 ---------- */

/**
 * 安全创建 DOM 元素
 * @param {string} tag
 * @param {string} [className]
 * @param {string} [text] - 文本内容（通过 textContent 设置，安全）
 * @returns {HTMLElement}
 */
function createEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
}

/* ---------- API 请求 ---------- */

/**
 * 统一 fetch 封装，含超时 + 错误处理
 * @param {string} url
 * @param {object} [options] - fetch options
 * @param {number} [timeout=15000] - 超时毫秒数
 * @returns {Promise<object>} 解析后的 JSON
 */
async function fetchJSON(url, options, timeout) {
    options = options || {};
    if (options.method && !['GET', 'HEAD', 'OPTIONS'].includes(options.method.toUpperCase())) {
        const sessionResponse = await fetchJSON('/api/session');
        options.headers = new Headers(options.headers || {});
        options.headers.set('X-CSRF-Token', sessionResponse.data.csrf_token);
    }
    const ms = timeout || 15000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);

    try {
        const resp = await fetch(url, { ...options, signal: controller.signal });
        const data = await resp.json();
        clearTimeout(timer);
        if (!resp.ok) {
            throw new Error(data.error || `HTTP ${resp.status}: ${resp.statusText}`);
        }
        if (resp.status === 202 && data.data && data.data.job_id) {
            const jobId = data.data.job_id;
            showToast('任务已排队：' + jobId + '，等待 worker 处理');
            for (let attempt = 0; attempt < 240; attempt++) {
                await new Promise(resolve => setTimeout(resolve, 1500));
                const job = await fetchJSON('/api/jobs/' + encodeURIComponent(jobId));
                if (job.data.status === 'done') return job.data.result;
                if (job.data.status === 'failed') throw new Error('任务失败，可重新提交。任务ID：' + jobId);
            }
            throw new Error('任务仍在执行，请稍后查询 /api/jobs/' + jobId);
        }
        if (data.success) noteFeatureSuccess(url);
        return data;
    } catch (e) {
        clearTimeout(timer);
        if (e.name === 'AbortError') {
            throw new Error('请求超时，请稍后重试');
        }
        throw e;
    }
}

/* ---------- 自愿功能推荐：仅记成功功能计数，不记页面轨迹 ---------- */
let featureRecorded = false;
async function noteFeatureSuccess(url) {
    const feature = location.pathname.slice(1);
    const endpoints = {dashboard:'/api/report', trend:'/api/trend', map:'/api/regions',
        graph:'/api/network', literature:'/api/literature', workspace:'/api/workspace/requests/'};
    if (featureRecorded || !endpoints[feature] || !url.startsWith(endpoints[feature])) return;
    featureRecorded = true;
    try {
        const auth = (await fetchJSON('/api/session')).data;
        if (!auth.shared_mode || !auth.user) return;
        const flags = (await fetchJSON('/api/product/status')).data;
        if (!flags.workspace) return;
        const prefs = (await fetchJSON('/api/workspace/preferences')).data;
        if (!prefs.suggestions) return;
        const headers = {'Content-Type':'application/json', 'X-Workspace-User':auth.user.id};
        await fetchJSON('/api/workspace/usage', {method:'POST', headers, body:JSON.stringify({feature})});
        const suggestion = (await fetchJSON('/api/workspace/suggestion', {method:'POST',headers,body:'{}'})).data;
        if (!suggestion) return;
        const card = createEl('aside', 'card'); card.setAttribute('aria-label','可关闭的功能建议');
        card.append(createEl('p', '', suggestion.message));
        const link = createEl('a', '', '查看推荐功能');
        if (!['graph','literature','dashboard'].includes(suggestion.feature)) return;
        link.href = '/' + suggestion.feature; card.append(link);
        const close = createEl('button', 'btn', '关闭推荐');
        close.onclick = async () => {card.remove();try{await fetchJSON('/api/workspace/preferences',
            {method:'PUT',headers,body:JSON.stringify({...prefs,suggestions:false})});}catch(_){showToast('关闭偏好未保存，请在个人工作区关闭');}};
        card.append(close); document.querySelector('main')?.append(card);
    } catch (_) { /* 可选推荐失败不影响业务功能。 */ }
}

/* ---------- 风险等级映射 ---------- */

/**
 * 风险等级 → CSS 类名
 * @param {string} level - 中文或英文等级
 * @returns {string} CSS class
 */
function riskLevelClass(level) {
    if (!level) return '';
    const l = String(level).toLowerCase();
    if (l === 'high' || l === '高风险') return 'high';
    if (l === 'medium' || l === '中风险') return 'medium';
    if (l === 'low' || l === '低风险') return 'low';
    return '';
}

/**
 * 风险分数 → 颜色 (hex)
 * @param {number} score 0-100
 * @returns {string}
 */
function riskScoreColor(score) {
    if (score >= 70) return 'var(--risk-high)';
    if (score >= 40) return 'var(--risk-medium)';
    return 'var(--risk-low)';
}

/* ---------- 数值格式化 ---------- */

function formatNumber(num, decimals) {
    if (num === null || num === undefined || !Number.isFinite(Number(num))) return '无数据';
    const d = decimals !== undefined ? decimals : 2;
    return Number(num).toFixed(d);
}

function formatPercent(num) {
    if (num === null || num === undefined || !Number.isFinite(Number(num))) return '无数据';
    return (Number(num) * 100).toFixed(1) + '%';
}

/* ---------- Toast 通知 ---------- */

/**
 * 显示 toast 通知
 * @param {string} msg
 * @param {string} [type='info'] - 'info' | 'error' | 'success'
 */
function showToast(msg, type) {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = createEl('div', 'toast-container');
        document.body.appendChild(container);
    }
    const toast = createEl('div', 'toast ' + (type || 'info'), msg);
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 350);
    }, 3500);
}

/* ---------- 加载态管理 ---------- */

function renderLoading(container, msg) {
    if (!container) return;
    container.innerHTML = '';
    const wrap = createEl('div', 'loading-spinner');
    const radar = createEl('div', 'loader-radar');
    radar.appendChild(createEl('div', 'sweep'));
    radar.appendChild(createEl('div', 'core'));
    const text = createEl('p', 'loading-text', msg || '正在加载地理数据…');
    wrap.appendChild(radar);
    wrap.appendChild(text);
    container.appendChild(wrap);
}

function hideLoading(container) {
    if (!container) return;
    const spinner = container.querySelector('.loading-spinner');
    if (spinner) spinner.remove();
}

/* ---------- 环形仪表 SVG ---------- */

/**
 * 生成环形仪表 SVG HTML
 * @param {number} value 0-100
 * @param {number} [size=120]
 * @param {string} [label]
 * @returns {string} safe HTML string (no user input)
 */
function gaugeRingHTML(value, size, label) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return '<div class="empty-state">无有效风险观测</div>';
    const s = size || 120;
    const r = (s - 16) / 2;
    const circ = 2 * Math.PI * r;
    const offset = circ * (1 - Math.max(0, Math.min(100, value)) / 100);
    const color = riskScoreColor(value);
    return `<div class="gauge-ring" style="width:${s}px;height:${s}px">
        <svg width="${s}" height="${s}">
            <circle class="gauge-bg" cx="${s/2}" cy="${s/2}" r="${r}"/>
            <circle class="gauge-fill" cx="${s/2}" cy="${s/2}" r="${r}"
                stroke="${color}" stroke-dasharray="${circ.toFixed(2)}"
                stroke-dashoffset="${offset.toFixed(2)}"/>
        </svg>
        <div class="gauge-text">
            <div class="gauge-value" style="color:${color}">${escapeHtml(String(Math.round(value)))}</div>
            <div class="gauge-label">${escapeHtml(label || '')}</div>
        </div>
    </div>`;
}

/* ---------- 指标条 HTML ---------- */

function metricBarHTML(name, value, max, color) {
    const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
    const c = color || riskScoreColor(pct);
    return `<div class="metric-bar">
        <div class="mb-header">
            <span class="mb-name">${escapeHtml(name)}</span>
            <span class="mb-value">${escapeHtml(formatNumber(value))}</span>
        </div>
        <div class="mb-track"><div class="mb-fill" style="width:${pct.toFixed(1)}%;background:${c}"></div></div>
    </div>`;
}

/* ---------- 四页共用的数据视图筛选 ---------- */
function viewQuery(extra, filters) {
    var params = new URLSearchParams();
    Object.entries(Object.assign({}, filters || readViewFilters(), extra || {})).forEach(function (pair) {
        if (pair[1] !== null && pair[1] !== undefined && pair[1] !== '') params.set(pair[0], pair[1]);
    });
    return params.toString();
}

function readViewFilters() {
    var result = {};
    ['days', 'end_date', 'region', 'source'].forEach(function (key) {
        var el = document.getElementById(key === 'days' ? 'days-select' : key + '-filter');
        if (el && el.value.trim()) result[key] = el.value.trim();
        else if (el && key === 'end_date') result[key] = el.dataset.invalidDate || 'invalid';
    });
    result.days = result.days || '30';
    result.region = result.region || 'MMR';
    return result;
}

function syncViewNavigation(filters) {
    var common = {};
    ['days', 'end_date', 'region', 'source'].forEach(key => { if (filters) common[key] = filters[key]; });
    var query = viewQuery({}, common);
    document.querySelectorAll('.nav-links a').forEach(function (a) {
        var path = new URL(a.href, window.location.href).pathname;
        if (['/', '/dashboard', '/map', '/trend'].includes(path)) a.href = path + '?' + query;
    });
}

function initViewFilters(onChange) {
    var query = new URLSearchParams(window.location.search);
    ['days', 'end_date', 'region', 'source'].forEach(function (key) {
        var el = document.getElementById(key === 'days' ? 'days-select' : key + '-filter');
        if (!el) return;
        if (key === 'days' && query.has(key) && !Array.from(el.options).some(o => o.value === query.get(key))) {
            el.add(new Option(query.get(key) + '天', query.get(key)));
        }
        if (query.has(key)) el.value = query.get(key);
        if (key === 'end_date' && query.has(key) && !el.value) el.dataset.invalidDate = query.get(key) || 'invalid';
        if (key === 'end_date' && !query.has(key) && !el.value) {
            var parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Yangon', year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(new Date());
            var date = Object.fromEntries(parts.map(p => [p.type, p.value]));
            el.value = date.year + '-' + date.month + '-' + date.day;
        }
        el.addEventListener('change', function () { syncViewNavigation(readViewFilters()); if (onChange) onChange(); });
    });
    document.querySelectorAll('[data-report-format]').forEach(function (a) {
        a.addEventListener('click', function (event) {
            if (a.getAttribute('aria-disabled') === 'true') event.preventDefault();
        });
    });
    syncViewNavigation(readViewFilters());
}

function setViewReport(snapshot) {
    document.querySelectorAll('[data-report-format]').forEach(function (a) {
        var ready = !!(snapshot && snapshot.revision && snapshot.report_available !== false);
        a.setAttribute('aria-disabled', ready ? 'false' : 'true');
        a.style.opacity = ready ? '1' : '0.5';
        a.title = ready ? '导出当前筛选；数据更新后须刷新' : '等待当前窗口加载成功后导出';
        if (!ready) { a.removeAttribute('href'); return; }
        a.href = '/api/report?' + viewQuery({format:a.dataset.reportFormat, revision:snapshot.revision}, snapshot.filters);
    });
}

/* ---------- 产品入口与筛选解释 ---------- */
document.addEventListener('DOMContentLoaded', async function () {
    const nav = document.querySelector('.nav-links');
    if (nav) {
        nav.style.flexWrap = 'wrap';
        [['/literature','文献中心'], ['/workspace','个人工作区'], ['/guide','使用说明']].forEach(([path,label]) => {
            if (!nav.querySelector('a[href="' + path + '"]')) {
                const li = createEl('li'); const a = createEl('a', '', label); a.href = path; li.append(a); nav.append(li);
            }
        });
    }
    const container = document.querySelector('.view-filters') || document.querySelector('.map-sidebar');
    if (!container) return;
    const help = createEl('details', 'filter-help');
    help.append(createEl('summary', '', '这些筛选条件是什么意思？'));
    help.append(createEl('p', '', 'MMR=缅甸全国；省邦代码限定事件发生地。来源留空=全部；单来源不提供全国综合分。正式观测=真实资料计算的系列，不代表已人工验证；旧版历史仅作对照，演示不是真实数据。缺测不能解释为零风险。'));
    const link = createEl('a', '', '查看各模块的含义与边界'); link.href = '/guide'; help.append(link); container.append(help);
    try {
        const response = await fetchJSON('/api/view-options');
        if (!response.success) return;
        for (const [id, rows] of [['region-filter', response.data.regions], ['source-filter', response.data.sources.map(s => ({code:s,name:s}))]]) {
            const input = document.getElementById(id);
            if (!input || input.tagName !== 'INPUT') continue;
            const list = createEl('datalist'); list.id = id + '-options';
            rows.forEach(row => {const option = createEl('option'); option.value = row.code; option.label = row.name; list.append(option);});
            input.setAttribute('list', list.id); input.insertAdjacentElement('afterend', list);
        }
    } catch (_) { /* 辅助列表失败不阻止原页面查询。 */ }
});

/* ---------- 页脚注入 ---------- */

document.addEventListener('DOMContentLoaded', function () {
    if (document.querySelector('.footer')) return;
    const footer = document.createElement('footer');
    footer.className = 'footer';
    footer.innerHTML = `<div class="footer-lab">华东师范大学 地缘环境智能计算实验室</div>
        <div class="footer-team">舒媛媛 杨雯瑾 高一翔 刘彦均 薛雨恬</div>`;
    document.body.appendChild(footer);
});
