/* 文献中心：使用安全 DOM，响应乱序保护，不加载外部脚本。 */
(() => {
    const el = id => document.getElementById(id);
    let offset = 0, sequence = 0, facetsReady = false;
    const pageSize = 20;
    const initial = new URLSearchParams(location.search);
    let recordId = initial.get('record');
    const names = {conflict_frequency:'冲突频次', event_severity:'事件严重度', china_myanmar_relation:'中缅关系',
        china_us_rivalry:'中美博弈', neighbor_relation:'邻国关系', western_relation:'西方关系',
        nightlight_change:'夜光变化', displacement:'流离失所', intl_sanction:'国际制裁'};
    function add(parent, tag, text) {
        const node = document.createElement(tag); node.textContent = text || ''; parent.append(node); return node;
    }
    function links(parent, record) {
        for (const item of record.links || []) {
            let url; try { url = new URL(item.url); } catch (_) { continue; }
            if (!['http:', 'https:'].includes(url.protocol)) continue;
            const a = add(parent, 'a', item.label); a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer';
            add(parent, 'span', '　');
        }
        if (!record.fulltext_available) add(parent, 'p', '暂无已审核开放全文链接；DOI 链接不代表可免费下载。');
    }
    async function load() {
        const seq = ++sequence;
        el('catalog-prev').disabled = el('catalog-next').disabled = true;
        el('catalog-results').replaceChildren(); el('catalog-summary').textContent = '检索中…';
        const query = new URLSearchParams({offset, limit:pageSize, q:el('catalog-q').value});
        for (const [key, id] of [['topic','catalog-topic'],['module','catalog-module'],['indicator','catalog-indicator'],['year_from','catalog-from'],['year_to','catalog-to']]) {
            if (el(id).value) query.set(key, el(id).value);
        }
        if (el('catalog-core').checked) query.set('core_only','true');
        try {
            const response = await fetchJSON('/api/literature?' + query);
            if (seq !== sequence) return;
            if (!response.success) throw new Error(response.error || '检索失败');
            const data = response.data, info = data.overview;
            if(recordId){
                const detail=await fetchJSON('/api/literature/'+encodeURIComponent(recordId));
                if(seq!==sequence)return;
                data.results=[detail.data];data.total=1;
            }
            el('catalog-summary').textContent = `共 ${info.record_count} 篇题录 · 核心标记 ${info.core_count} 篇 · 已审核开放全文 ${info.fulltext_count} 篇 · 匹配 ${data.total} 篇`;
            if (!facetsReady) {
                for (const [field,id] of [['topic','catalog-topic'],['indicator','catalog-indicator']]) {
                    for (const key of Object.keys(info.facets[field] || {})) el(id).add(new Option(names[key] || key,key));
                }
                facetsReady = true;
            }
            for (const rec of data.results) {
                const card = document.createElement('article'); card.className = 'card';
                add(card, 'h2', rec.title);
                add(card, 'p', `${(rec.authors || []).join('、')} · ${rec.year || '年份待核'} · ${rec.journal || '期刊未注明'}`);
                add(card, 'p', (rec.topics || []).join(' / '));
                add(card, 'p', '关联模块：'+(rec.modules || []).join('、')); 
                const details = document.createElement('details'); add(details, 'summary', '摘要、指标关联与全文入口');
                add(details, 'p', rec.abstract || '未收录摘要');
                add(details, 'p', '指标：' + (rec.indicators || []).map(k => names[k] || k).join('、'));
                links(details, rec); card.append(details); el('catalog-results').append(card);
            }
            if (!data.results.length) add(el('catalog-results'), 'p', '没有匹配题录。索引缺失时请由管理员配置私密源表并显式构建。');
            el('catalog-page').textContent = data.total ? `${offset + 1}—${Math.min(offset + pageSize,data.total)} / ${data.total}` : '0 / 0';
            el('catalog-prev').disabled = offset === 0;
            el('catalog-next').disabled = offset + pageSize >= data.total;
        } catch (error) {
            if (seq !== sequence) return;
            el('catalog-summary').textContent = error.message; el('catalog-page').textContent = '';
        }
    }
    el('catalog-form').addEventListener('submit', event => {event.preventDefault(); recordId=null; offset = 0; load();});
    el('catalog-prev').onclick = () => {offset = Math.max(0,offset-pageSize); load();};
    el('catalog-next').onclick = () => {offset += pageSize; load();};
    el('catalog-q').value=initial.get('q')||'';
    if(initial.get('module'))el('catalog-module').value=initial.get('module');
    load();
})();
