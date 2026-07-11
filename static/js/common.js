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
    const ms = timeout || 15000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);

    try {
        const resp = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(timer);
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }
        return await resp.json();
    } catch (e) {
        clearTimeout(timer);
        if (e.name === 'AbortError') {
            throw new Error('请求超时，请稍后重试');
        }
        throw e;
    }
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
    if (num === null || num === undefined) return 'N/A';
    const d = decimals !== undefined ? decimals : 2;
    return Number(num).toFixed(d);
}

function formatPercent(num) {
    if (num === null || num === undefined) return 'N/A';
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

function renderLoading(container) {
    if (!container) return;
    container.innerHTML = '';
    const wrap = createEl('div', 'loading-spinner');
    const spinner = createEl('div', 'spinner');
    const text = createEl('p', 'loading-text', '加载中...');
    wrap.appendChild(spinner);
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

/* ---------- 页脚注入 ---------- */

document.addEventListener('DOMContentLoaded', function () {
    if (document.querySelector('.footer')) return;
    const footer = document.createElement('footer');
    footer.className = 'footer';
    footer.innerHTML = `<div class="footer-lab">华东师范大学 地缘环境智能计算实验室</div>
        <div class="footer-team">舒媛媛 杨雯瑾 高一翔 刘彦均 薛雨恬</div>`;
    document.body.appendChild(footer);
});
