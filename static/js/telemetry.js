/* 指挥栏遥测：UTC 时钟 + 服务健康灯。只读 GET /health（不触发采集或分析），失败只改灯色。 */
(() => {
    const box = document.querySelector('.nav-telemetry');
    if (!box) return;
    const clock = box.querySelector('.tm-clock');
    const label = box.querySelector('.tm-label');
    const pad = n => String(n).padStart(2, '0');
    const tick = () => {
        const d = new Date();
        clock.textContent = `UTC ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
        clock.dateTime = d.toISOString();
    };
    const setState = (state, text) => {
        box.dataset.state = state;
        label.textContent = text;
        box.setAttribute('aria-label', `Web 服务连接状态：${text}`);
        box.title = `${state === 'ok' ? `Web 服务连接正常（${new Date().toLocaleTimeString()}）` : text}；仅表示服务连通，不代表数据完整或结论已验证。`;
    };
    let checking = false;
    const check = async () => {
        if (checking || document.hidden) return;
        checking = true;
        setState('checking', '服务检测中');
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        try {
            const resp = await fetch('/health', { cache: 'no-store', signal: controller.signal });
            const json = await resp.json();
            if (resp.ok && json.status === 'ok') setState('ok', '服务在线');
            else setState('down', '服务异常');
        } catch (e) {
            setState('down', e.name === 'AbortError' ? '检查超时' : '连接中断');
        } finally {
            clearTimeout(timeout);
            checking = false;
        }
    };
    tick();
    setInterval(tick, 1000);
    check();
    setInterval(check, 60000);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) { tick(); check(); }
    });
})();
