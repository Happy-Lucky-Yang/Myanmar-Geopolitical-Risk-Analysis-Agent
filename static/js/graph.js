/**
 * 关系网络图谱页（ECharts 力导向图）
 * 三种视图：文章实体共现（正式观测）/ 知识图谱种子（演示域）/ 国家行为体网络（8节点有向）
 * 语义边界：共现≠合作/冲突/因果；行为体网络不进入风险合成。
 */
let graphChart = null;

const CATEGORY_COLORS = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff', '#39c5cf', '#ff7b72', '#7ee787'];

function graphFilters() {
    return {
        days: (document.getElementById('days-select') || {}).value || '30',
        end_date: (document.getElementById('end_date-filter') || {}).value || '',
        region: (document.getElementById('region-filter') || {}).value || 'MMR',
        source: (document.getElementById('source-filter') || {}).value || ''
    };
}

function ensureChart() {
    if (!graphChart) {
        graphChart = echarts.init(document.getElementById('graph-chart'));
        window.addEventListener('resize', () => graphChart && graphChart.resize());
    }
    return graphChart;
}

function degreeMap(edges) {
    const deg = {};
    (edges || []).forEach(e => {
        deg[e.source] = (deg[e.source] || 0) + 1;
        deg[e.target] = (deg[e.target] || 0) + 1;
    });
    return deg;
}

function showEmpty(msg) {
    ensureChart().setOption({
        backgroundColor: 'transparent',
        title: { text: msg, left: 'center', top: 'middle', textStyle: { color: '#8b949e', fontSize: 14 } },
        series: []
    }, true);
}

function renderEntityGraph(data, categoryField) {
    const graph = data.graph || {};
    const rawNodes = graph.nodes || [];
    const rawEdges = graph.edges || [];
    if (!rawNodes.length) { showEmpty('窗口内没有可用实体证据（未用演示数据回填）'); return; }
    const deg = degreeMap(rawEdges);
    const catNames = [];
    rawNodes.forEach(n => {
        const c = n[categoryField] || n.entity_type || '其他';
        if (!catNames.includes(c)) catNames.push(c);
    });
    const nodes = rawNodes.map(n => ({
        id: String(n.id),
        name: n.name || String(n.id),
        category: catNames.indexOf(n[categoryField] || n.entity_type || '其他'),
        symbolSize: Math.min(46, 10 + (deg[n.id] || 0) * 2.2),
        entity_type: n[categoryField] || n.entity_type || '',
        degree: deg[n.id] || 0
    }));
    const idSet = new Set(nodes.map(n => n.id));
    const links = rawEdges.filter(e => idSet.has(String(e.source)) && idSet.has(String(e.target))).map(e => ({
        source: String(e.source), target: String(e.target),
        value: e.weight || 1, rel: e.type || ''
    }));
    ensureChart().setOption({
        backgroundColor: 'transparent',
        tooltip: {
            formatter: p => {
                if (p.dataType === 'edge') return escapeHtml(`${p.data.source} ↔ ${p.data.target}<br/>共现权重 ${p.data.value}`);
                return escapeHtml(`<b>${p.data.name}</b><br/>类型 ${p.data.entity_type}<br/>度 ${p.data.degree}`);
            }
        },
        legend: [{ data: catNames, textStyle: { color: '#8b949e' }, top: 6 }],
        series: [{
            type: 'graph', layout: 'force', roam: true, draggable: true,
            categories: catNames.map((c, i) => ({ name: c, itemStyle: { color: CATEGORY_COLORS[i % CATEGORY_COLORS.length] } })),
            label: { show: true, position: 'right', color: '#c9d1d9', fontSize: 11 },
            force: { repulsion: 240, edgeLength: [60, 150], gravity: 0.08 },
            data: nodes, links: links,
            lineStyle: { color: '#6e7681', curveness: 0.12, opacity: 0.65, width: 1.4 },
            emphasis: { focus: 'adjacency', lineStyle: { width: 3 } }
        }]
    }, true);
}

function renderActorGraph(data) {
    const rawNodes = data.nodes || [];
    const rawEdges = data.edges || [];
    if (!rawNodes.length) { showEmpty('行为体网络为空'); return; }
    const nodes = rawNodes.map(n => ({
        id: n.id, name: n.name, symbolSize: n.id === 'MMR' ? 56 : 40,
        role: n.role || '', indicator: n.indicator || '',
        itemStyle: { color: n.id === 'MMR' ? '#58a6ff' : (n.indicator ? '#d29922' : '#8b949e') },
        label: { show: true, color: '#e6edf3', fontSize: 12, fontWeight: 'bold' }
    }));
    const links = rawEdges.map(e => ({
        source: e.source, target: e.target, value: e.weight,
        layer: e.layer, mentions: e.mentions, event_count: e.event_count,
        lineStyle: { color: e.layer === 'cooperation' ? '#3fb950' : (e.layer === 'conflict' ? '#f85149' : '#8b949e'), width: Math.min(8, 1 + Math.abs(e.weight) / 2), opacity: 0.85, curveness: 0.18 }
    }));
    ensureChart().setOption({
        backgroundColor: 'transparent',
        tooltip: {
            formatter: p => {
                if (p.dataType === 'edge') {
                    const d = p.data;
                    return escapeHtml(`${d.source} → ${d.target}<br/>层 ${d.layer}<br/>Goldstein 净值 ${d.value}<br/>报道量 ${d.mentions} / 事件 ${d.event_count}`);
                }
                return escapeHtml(`<b>${p.data.name}</b><br/>角色 ${p.data.role}<br/>对应指标 ${p.data.indicator || '—'}`);
            }
        },
        series: [{
            type: 'graph', layout: 'force', roam: true, draggable: true,
            data: nodes, links: links,
            edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 9,
            force: { repulsion: 420, edgeLength: [120, 200], gravity: 0.06 },
            emphasis: { focus: 'adjacency' }
        }]
    }, true);
}

function renderStats(view, data) {
    const stats = document.getElementById('graph-stats');
    const top = document.getElementById('graph-top');
    const sem = document.getElementById('graph-semantics');
    const md = data.metadata || {};
    let html = '';
    if (view === 'actors') {
        const coop = (data.layers && data.layers.cooperation || []).length;
        const conf = (data.layers && data.layers.conflict || []).length;
        const mb = (data.centrality && data.centrality.mmr_betweenness) || {};
        html = `<p>节点 <b>${data.node_count}</b> · 有向边 <b>${data.edge_count}</b> · 合作层 ${coop} / 冲突层 ${conf}</p>
            <p>窗口事件 <b>${md.event_count || 0}</b> 条 · 边权 = Goldstein 报道量加权净值 ∈[-10,10]</p>
            <p>缅甸中介中心性：合作 ${mb.cooperation ?? '—'} / 冲突 ${mb.conflict ?? '—'}（${mb.more_conflict_hub ? '更偏冲突枢纽' : '更偏合作枢纽'}）</p>
            <p>进入风险合成：<b>否</b>（关系层五项指标已以双边配对进入九指标合成，避免重复计量）</p>`;
        const cen = (data.centrality && data.centrality.layers) || {};
        let topHtml = '';
        ['cooperation', 'conflict'].forEach(layer => {
            const b = (cen[layer] && cen[layer].betweenness) || {};
            const items = Object.entries(b).sort((a, c) => c[1] - a[1]).slice(0, 4)
                .map(([k, v]) => `${k} ${v}`).join('、');
            topHtml += `<p><b>${layer === 'cooperation' ? '合作层' : '冲突层'}中介中心性</b>：${escapeHtml(items || '—')}</p>`;
            const mod = cen[layer] && cen[layer].modularity;
            topHtml += `<p>模块度 ${mod ?? '—'}</p>`;
        });
        top.innerHTML = topHtml || '<p>—</p>';
    } else {
        html = `<p>节点 <b>${data.node_count}</b> · 边 <b>${data.edge_count}</b> · 密度 ${data.density ?? '—'} · 平均度 ${data.avg_degree ?? '—'} · 连通分量 ${data.components ?? '—'}</p>
            <p>文章数 ${md.article_count ?? '—'} · 数据域 ${escapeHtml(md.data_domain ?? '—')} · 算法 ${escapeHtml(md.algorithm_version ?? '—')}</p>
            <p>窗口 ${escapeHtml(md.requested_start ?? '—')} ~ ${escapeHtml(md.end_exclusive ?? '—')}</p>`;
        const tops = (data.top_actors || []).slice(0, 6)
            .map(a => `<li>${escapeHtml(a.name)}（度中心性 ${a.degree_centrality}，${escapeHtml(a.role)}）</li>`).join('');
        const coms = (data.communities || []).slice(0, 3)
            .map(c => `<li>社区${c.id}（${c.size}）：${escapeHtml((c.members || []).join('、'))}</li>`).join('');
        top.innerHTML = `<p><b>关键节点</b></p><ul>${tops || '<li>—</li>'}</ul><p><b>社区</b></p><ul>${coms || '<li>—</li>'}</ul>`;
    }
    stats.innerHTML = html;
    sem.innerHTML = `<p>${escapeHtml(md.relation_semantics || '—')}</p>` +
        (md.boundary ? `<p>${escapeHtml(md.boundary)}</p>` : '') +
        (md.warning ? `<p>${escapeHtml(md.warning)}</p>` : '');
}

async function loadGraph() {
    const view = (document.getElementById('graph-view') || {}).value || 'cooccur';
    const f = graphFilters();
    let url;
    if (view === 'cooccur') {
        url = `/api/network?domain=observed&days=${f.days}&region=${encodeURIComponent(f.region)}` +
            (f.end_date ? `&end_date=${f.end_date}` : '') + (f.source ? `&source=${encodeURIComponent(f.source)}` : '');
    } else if (view === 'kg') {
        url = '/api/network?domain=demo';
    } else {
        url = `/api/network/actors?days=${f.days}` + (f.end_date ? `&end_date=${f.end_date}` : '');
    }
    try {
        const res = await fetchJSON(url, {}, 30000);
        if (!res || res.success === false) { showEmpty((res && res.error) || '加载失败'); return; }
        const data = res.data || {};
        if (view === 'actors') { renderActorGraph(data); }
        else { renderEntityGraph(data, view === 'kg' ? 'type' : 'entity_type'); }
        renderStats(view, data);
    } catch (e) {
        showEmpty('请求失败：' + (e && e.message ? e.message : e));
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const viewSel = document.getElementById('graph-view');
    if (viewSel) viewSel.addEventListener('change', loadGraph);
    loadGraph();
});
