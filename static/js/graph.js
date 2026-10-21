/**
 * 关系网络图谱页（ECharts 力导向图）
 * 三种视图：文章实体共现（正式观测）/ 知识图谱种子（演示域）/ 国家行为体网络（8节点有向）
 * 语义边界：共现≠合作/冲突/因果；行为体网络不进入风险合成。
 */
let graphChart = null;

// 浅色「晨雾」分类色：玉青、静蓝、杏黄、珊瑚、薰衣草、湖蓝、蘑粉、苔绿
const CATEGORY_COLORS = ['#3a9a8a', '#6c93d6', '#e0a64a', '#db665e', '#9787cc', '#4fb0c6', '#e58fa5', '#8bb66a'];
const GRAPH_TOOLTIP = {
    backgroundColor: 'rgba(255,255,255,0.96)', borderColor: '#e2e9e6', textStyle: { color: '#24333b', fontSize: 12 },
    extraCssText: 'border-radius:12px;box-shadow:0 12px 28px -14px rgba(36,51,59,0.35);'
};
// 实体类型中文显示名；未知类型保持原样
const ENTITY_LABELS = { Location: '地点', Person: '人物', Organization: '组织', Org: '组织', Event: '事件', Country: '国家' };
const entityLabel = t => ENTITY_LABELS[t] || t;

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
        title: { text: msg, left: 'center', top: 'middle', textStyle: { color: '#93a2aa', fontSize: 14, fontWeight: 400 } },
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
        const c = entityLabel(n[categoryField] || n.entity_type || '其他');
        if (!catNames.includes(c)) catNames.push(c);
    });
    // 节点少时放大节点与间距，让结构铺满画布；节点多时保持紧凑。
    const sparse = rawNodes.length < 40;
    const nodes = rawNodes.map(n => ({
        id: String(n.id),
        name: n.name || String(n.id),
        category: catNames.indexOf(entityLabel(n[categoryField] || n.entity_type || '其他')),
        symbolSize: sparse ? Math.min(66, 20 + (deg[n.id] || 0) * 5) : Math.min(46, 10 + (deg[n.id] || 0) * 2.2),
        entity_type: entityLabel(n[categoryField] || n.entity_type || ''),
        degree: deg[n.id] || 0
    }));
    const idSet = new Set(nodes.map(n => n.id));
    const links = rawEdges.filter(e => idSet.has(String(e.source)) && idSet.has(String(e.target))).map(e => ({
        source: String(e.source), target: String(e.target),
        value: e.weight || 1, rel: e.type || '',
        lineStyle: { width: 1.2 + Math.min(5, Math.log2(1 + Math.abs(Number(e.weight) || 1)) * 1.4) }
    }));
    ensureChart().setOption({
        backgroundColor: 'transparent',
        tooltip: Object.assign({}, GRAPH_TOOLTIP, {
            formatter: p => {
                if (p.dataType === 'edge') return `${escapeHtml(p.data.source)} ↔ ${escapeHtml(p.data.target)}<br/>共现权重 ${escapeHtml(p.data.value)}`;
                return `<b>${escapeHtml(p.data.name)}</b><br/>类型 ${escapeHtml(p.data.entity_type)}<br/>度 ${escapeHtml(p.data.degree)}`;
            }
        }),
        legend: [{ data: catNames, textStyle: { color: '#576873' }, top: 6, icon: 'circle', itemWidth: 9, itemHeight: 9 }],
        series: [{
            type: 'graph', layout: 'force', roam: true, draggable: true,
            categories: catNames.map((c, i) => ({ name: c, itemStyle: { color: CATEGORY_COLORS[i % CATEGORY_COLORS.length], borderColor: '#ffffff', borderWidth: 1.5, shadowBlur: 8, shadowColor: 'rgba(36,51,59,0.15)' } })),
            label: { show: true, position: 'right', color: '#3c4c56', fontSize: sparse ? 13 : 11, fontWeight: sparse ? 600 : 400 },
            force: sparse ? { repulsion: 520, edgeLength: [90, 170], gravity: 0.12 } : { repulsion: 240, edgeLength: [60, 150], gravity: 0.08 },
            data: nodes, links: links,
            lineStyle: { color: '#b9c7c3', curveness: 0.14, opacity: 0.7, width: 1.3 },
            emphasis: { focus: 'adjacency', lineStyle: { width: 3, color: '#3a9a8a' } }
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
        itemStyle: { color: n.id === 'MMR' ? '#3a9a8a' : (n.indicator ? '#e0a64a' : '#a9b6bd'), borderColor: '#ffffff', borderWidth: 2, shadowBlur: 12, shadowColor: 'rgba(36,51,59,0.18)' },
        label: { show: true, color: '#24333b', fontSize: 12, fontWeight: 'bold' }
    }));
    const links = rawEdges.map(e => ({
        source: e.source, target: e.target, value: e.weight,
        layer: e.layer, mentions: e.mentions, event_count: e.event_count,
        lineStyle: { color: e.layer === 'cooperation' ? '#4ca37c' : (e.layer === 'conflict' ? '#db665e' : '#b9c7c3'), width: Math.min(8, 1 + Math.abs(e.weight) / 2), opacity: 0.8, curveness: 0.18 }
    }));
    ensureChart().setOption({
        backgroundColor: 'transparent',
        tooltip: Object.assign({}, GRAPH_TOOLTIP, {
            formatter: p => {
                if (p.dataType === 'edge') {
                    const d = p.data;
                    return `${escapeHtml(d.source)} → ${escapeHtml(d.target)}<br/>层 ${escapeHtml(d.layer)}<br/>Goldstein 净值 ${escapeHtml(d.value)}<br/>报道量 ${escapeHtml(d.mentions)} / 事件 ${escapeHtml(d.event_count)}`;
                }
                return `<b>${escapeHtml(p.data.name)}</b><br/>角色 ${escapeHtml(p.data.role)}<br/>对应指标 ${escapeHtml(p.data.indicator || '—')}`;
            }
        }),
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
    initViewFilters(loadGraph);
    const viewSel = document.getElementById('graph-view');
    if (viewSel) viewSel.addEventListener('change', loadGraph);
    loadGraph();
});
