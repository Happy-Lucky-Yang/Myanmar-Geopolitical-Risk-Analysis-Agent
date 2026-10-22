/* 仪表盘轻量 AI 风险解读：同步调用 /api/risk/explain，模型不可用时自动降级为规则解读。 */
(() => {
    const btn = document.getElementById('risk-explain-btn');
    const select = document.getElementById('explain-model');
    const area = document.getElementById('risk-explain-body');
    if (!btn || !area) return;
    let sequence = 0;

    function line(text, className) {
        const node = document.createElement('p');
        node.textContent = text;
        if (className) node.className = className;
        area.append(node);
        return node;
    }

    const MODEL_LABELS = {
        'glm-4-flash': 'GLM-4-Flash', 'glm-4-plus': 'GLM-4-Plus', 'glm-4-long': 'GLM-4-Long',
        'deepseek-chat': 'DeepSeek-chat', 'deepseek-reasoner': 'DeepSeek-reasoner',
        'rule-based': '规则解读（未调用模型）'
    };

    function render(data) {
        area.replaceChildren();
        const answer = data.answer || {};
        const modelLabel = MODEL_LABELS[data.model_version] || data.model_version || '—';
        const status = data.status === 'ok' ? '' : '（规则解读，未调用大模型）';
        line(`${data.date || '—'} · 模型 ${modelLabel}${status}` + (data.cached ? ' · 缓存' : ''), 'sh-meta');
        if (answer.summary) line(answer.summary);
        if ((answer.top_drivers || []).length) {
            line('主要贡献', 'sh-meta');
            answer.top_drivers.forEach(d => line(`· ${d.indicator}：${d.points} 分`));
        }
        if ((answer.explanations || []).length) {
            line('解释', 'sh-meta');
            answer.explanations.forEach(x => line('· ' + x));
        }
        if ((answer.suggestions || []).length) {
            line('条件化建议', 'sh-meta');
            answer.suggestions.forEach(x => line('· ' + x));
        }
        if ((answer.data_gaps || []).length) {
            line('数据缺口', 'sh-meta');
            answer.data_gaps.forEach(x => line('· ' + x));
        }
        line('置信度：' + (answer.confidence || '—'), 'sh-meta');
        if (data.disclaimer) line(data.disclaimer, 'sh-meta');
    }

    btn.onclick = async () => {
        const epoch = ++sequence;
        btn.disabled = true;
        area.textContent = '正在请求解读（默认 GLM-4-Flash，通常数秒）…';
        try {
            const payload = {};
            if (select && select.value) payload.model = select.value;
            const response = await fetchJSON('/api/risk/explain', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }, 90000);
            if (epoch !== sequence) return;
            if (!response.success) { area.textContent = response.error || '解读失败'; return; }
            render(response.data);
        } catch (error) {
            if (epoch === sequence) area.textContent = error.message;
        } finally {
            if (epoch === sequence) btn.disabled = false;
        }
    };
})();
