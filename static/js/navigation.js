/* 共享导航：原生 dialog 提供焦点约束与 Esc 关闭，不发网络请求。 */
(() => {
    const dialog = document.getElementById('site-menu');
    const toggle = document.querySelector('.nav-menu-toggle');
    if (!dialog || !toggle) return;
    toggle.addEventListener('click', () => {
        if (dialog.open) return;
        dialog.showModal();
        toggle.setAttribute('aria-expanded', 'true');
        document.body.classList.add('menu-open');
    });
    dialog.querySelector('.menu-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const nodes = Array.from(dialog.querySelectorAll('button, a[href], summary, input, select, textarea, [tabindex]'))
            .filter(n => {
                if (n.disabled || n.tabIndex < 0 || !n.getClientRects().length) return false;
                for (let parent=n.parentElement;parent && parent!==dialog;parent=parent.parentElement) {
                    if (parent.tagName==='DETAILS' && !parent.open && parent.querySelector(':scope > summary')!==n) return false;
                }
                return true;
            });
        const first = nodes[0], last = nodes.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    });
    dialog.addEventListener('close', () => {
        toggle.setAttribute('aria-expanded', 'false');
        document.body.classList.remove('menu-open');
        toggle.focus();
    });
    dialog.addEventListener('click', event => {
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
})();
