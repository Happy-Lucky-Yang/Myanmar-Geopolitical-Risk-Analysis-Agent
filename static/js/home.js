/* 全国首页只读：同一修订风险／事件／地图；全局来源状态独立读取。 */
var homeSnapshot = null;
var homeSequence = 0;
var homeSourceSequence = 0;
var homeMapController = null;
const HOME_CAMEO = {'01':'公开声明','02':'呼吁','03':'合作意向','04':'磋商','05':'外交合作','06':'物质合作','07':'援助','08':'让步','09':'调查','10':'要求','11':'表示不满','12':'拒绝','13':'威胁','14':'抗议','15':'展示武力','16':'降低关系','17':'胁迫','18':'袭击','19':'交战','20':'大规模暴力'};
const HOME_SEVERITY = {'01':.05,'02':.08,'03':.08,'04':.10,'05':.12,'06':.15,'07':.15,'08':.18,'09':.20,'10':.22,'11':.25,'12':.28,'13':.35,'14':.45,'15':.55,'16':.65,'17':.70,'18':.78,'19':.88,'20':.95};
function homeNumber(v, decimals=1) { return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(decimals) : '—'; }
function homeDay() {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Yangon',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
    const d = Object.fromEntries(parts.map(p => [p.type,p.value]));
    return `${d.year}-${d.month}-${d.day}`;
}
function homeMessage(id, text, retry) {
    const area = document.getElementById(id);
    area.replaceChildren(createEl('p','module-error',text));
    if (retry) {
        const button = createEl('button','btn btn-secondary','重试读取');
        button.type = 'button'; button.onclick = retry; area.append(button);
    }
}
function homeDetailPath(path, extra={}) {
    return path + '?' + viewQuery({...extra,revision:homeSnapshot.revision},homeSnapshot.filters);
}
function homeComparable(a,b) { return sameRiskBasis(a,b); }
function homeEventSeverity(event) {
    const root = String(event.root_code || '').padStart(2,'0');
    const base = HOME_SEVERITY[root];
    if (String(event.quad_class) === '4') return Math.max(base || 0,.7);
    if (String(event.quad_class) === '3') return Math.max(base || 0,.4);
    return base ?? -1;
}
function homeDataFact(label, value, unit) {
    const fact = createEl('div','home-data-fact');
    fact.append(createEl('dt','',label));
    const amount = createEl('dd');
    amount.append(createEl('strong','',value), createEl('small','',unit));
    fact.append(amount);
    return fact;
}
function renderHomeSnapshot(snapshot) {
    const records = (snapshot.history || []).filter(r => typeof r.risk_score === 'number' && Number.isFinite(r.risk_score)).slice().sort((a,b) => String(a.date).localeCompare(String(b.date)));
    const latest = records.at(-1);
    const current = latest?.date === snapshot.filters.end_date;
    const day = new Date(snapshot.filters.end_date + 'T00:00:00Z'); day.setUTCDate(day.getUTCDate()-1);
    const previous = records.find(r => r.date === day.toISOString().slice(0,10));
    const delta = current && homeComparable(latest,previous) ? latest.risk_score - previous.risk_score : null;
    const details = latest?.details || {};
    const version = latest?.algorithm_version || details.algorithm_version || snapshot.metadata?.algorithm_version || '版本未提供';
    const level = current ? (latest.risk_score >= 70 ? '高风险' : latest.risk_score >= 40 ? '中风险' : '低风险') : '截止日无有效风险观测';
    const risk = document.getElementById('home-risk'); risk.replaceChildren();
    const scoreRow = createEl('div','home-score-row');
    const value = createEl('div','home-value',current ? homeNumber(latest.risk_score) : '—');
    value.append(createEl('small','',current ? ' 分／100' : ''));
    scoreRow.append(value,createEl('span','home-level',level)); risk.append(scoreRow);
    if (current) value.style.color = riskScoreColor(latest.risk_score);
    risk.append(createEl('p','home-score-meta',`观测日期：${latest?.date || '—'} · ${version}（日观测）`));
    risk.append(createEl('p','home-score-meta',`较前一日：${delta === null ? '—（无法同口径比较）' : (delta > 0 ? '+' : '') + homeNumber(delta) + '分'}`));
    if (!current && latest) risk.append(createEl('p','home-note',`历史参考：${latest.date}，${homeNumber(latest.risk_score)}分；观测已过期，不代表当前风险。`));
    const sample = latest?.news_count ?? details.news_count;
    if (typeof sample === 'number' && sample < 5) risk.append(createEl('p','home-warning',`样本偏少：${sample}条新闻，评分仅供参考。`));
    const anomaly = latest?.anomaly_flag || details.anomaly_flag;
    if (anomaly && anomaly !== 'low_sample') risk.append(createEl('p','home-warning',latest?.anomaly_reason || details.anomaly_reason || '极端分值，建议人工复核。'));
    const coverage = latest?.indicator_coverage ?? details.indicator_coverage;
    const missing = Object.entries(details.indicator_scores || latest?.indicator_scores || {}).filter(([,r]) => r.quality === 'missing' || r.value == null).length;
    if (typeof coverage === 'number' && coverage < .9999) risk.append(createEl('p','home-warning',`输入不完整${missing ? '：' + missing + '项指标缺测' : ''}，只按有效权重合成。`));
    const data = document.getElementById('home-data'); data.replaceChildren();
    const facts = createEl('dl','home-data-facts');
    facts.append(homeDataFact('日风险观测',String(snapshot.metadata?.valid_days ?? records.length),'／30个业务日'),
        homeDataFact('最新观测有效指标权重', typeof coverage === 'number' ? homeNumber(coverage*100) : '—', typeof coverage === 'number' ? '%' : '未提供'));
    data.append(facts);
    const qualityNotes = [];
    if (!current) qualityNotes.push(latest ? '截止日缺测，历史值不能作为当前预警。' : '截止日缺测，窗口内无有效风险观测。');
    if (typeof coverage === 'number' && coverage < .9999) qualityNotes.push('指标不全，当前日分不是完整九指标正式评价。');
    if (qualityNotes.length) data.append(createEl('p','home-warning',qualityNotes.join('')));
    (snapshot.warnings || []).forEach(w => data.append(createEl('p','home-warning',w)));
    document.getElementById('home-range').textContent = `近30个业务日 · 截至 ${snapshot.filters.end_date} · 缅甸全国`;
    for (const [id,path] of [['home-risk-link','/dashboard/indicators'],['home-data-link','/dashboard/data'],['home-events-link','/dashboard/events'],['home-map-link','/map']]) document.getElementById(id).href = homeDetailPath(path);
    document.getElementById('home-map').setAttribute('aria-label',`缅甸省级规则烈度地图。窗口内保存${(snapshot.events || []).length}条报道事件记录，仅成功定位且有烈度的记录参与填色，灰色表示缺测，不是现实零事件。`);
    const events = (snapshot.events || []).slice().sort((a,b) => String(b.date).localeCompare(String(a.date)) || homeEventSeverity(b)-homeEventSeverity(a) || String(a.event_id).localeCompare(String(b.event_id))).slice(0,3);
    const area = document.getElementById('home-events'); area.replaceChildren();
    if (!events.length) area.append(createEl('p','home-note','所选窗口无事件记录；不代表现实中没有事件。'));
    events.forEach(event => {
        const row = createEl('article','home-event');
        const kind = HOME_CAMEO[String(event.root_code || '').padStart(2,'0')] || event.event_type || '类型未标注';
        const location = event.location || event.event_location || '未定位';
        const link = createEl('a');
        link.href = homeDetailPath('/dashboard/events',{event_id:event.event_id,event_source:event.source});
        const verified = event.evidence_verified === true && event.independent_source_count >= 2;
        const eventMeta = createEl('small','home-event-meta',`${event.date || '日期未提供'} · ${event.source || 'GDELT'} · ${verified ? '已审核独立多源' : '未核验'}`);
        link.append(eventMeta);
        link.append(createEl('span','home-event-title',`${location} · ${kind}`));
        if (event.event_id != null) {
            const identity = createEl('span','home-event-identity',`#${event.event_id}`);
            identity.title = '来源内记录编号；不同编号不代表已证实的不同现实事件。';
            eventMeta.append(identity);
        }
        if (event.description) link.append(createEl('p','',String(event.description).slice(0,80) + (event.description.length > 80 ? '…' : '')));
        row.append(link); area.append(row);
    });
}
async function loadHomeSources() {
    const seq = ++homeSourceSequence;
    try {
        const json = await fetchJSON('/api/sources/health');
        if (seq !== homeSourceSequence) return;
        if (!json.success) throw Error(json.error || '来源状态读取失败');
        const rows = Object.values(json.data || {});
        const successful = rows.filter(r => r.records?.at(-1)?.ok === true).length;
        const stamps = rows.map(r => r.records?.at(-1)?.time).filter(Boolean).sort();
        const stamp = stamps.at(-1);
        const total = json.active_count ?? rows.length;
        const text = `最近采集成功：${successful}／${total}个在用来源；各源时间不同`;
        const area = document.getElementById('home-sources'); area.replaceChildren(createEl('p','',total ? text : json.active_count === 0 ? '暂无启用来源。' : '暂无来源运行记录。'));
        if (stamp) area.append(createEl('p','',`最近一次全局运行：${stamp.replace('T',' ').slice(0,19)}（UTC）；不随窗口裁剪`));
        if (total > rows.length) area.append(createEl('p','',`${total - rows.length}个在用来源尚无运行记录，未计作成功。`));
    } catch (error) {
        if (seq === homeSourceSequence) homeMessage('home-sources','采集运行状态暂不可用；不据此判断所有来源失联。',loadHomeSources);
    }
}
async function loadHomeMap(snapshot, seq) {
    if (homeMapController) homeMapController.abort();
    const controller = homeMapController = new AbortController();
    const timer = setTimeout(() => controller.abort(),30000);
    homeMessage('home-map','正在读取同一修订的地图预览…');
    try {
        const response = await fetch('/api/map/unified?' + viewQuery({compact:1,revision:snapshot.revision},snapshot.filters),{signal:controller.signal});
        if (seq !== homeSequence) return;
        if (!response.ok) throw Error(response.status === 409 ? '数据版本已更新，请重新读取观测。' : '地图读取失败');
        if (response.headers.get('X-Data-Revision') !== snapshot.revision) throw Error('地图修订不一致，未展示混合版本。');
        const html = await response.text();
        if (seq !== homeSequence) return;
        document.getElementById('home-map').innerHTML = html;
        const frame = document.querySelector('#home-map iframe');
        if (!frame) throw Error('地图预览为空');
        frame.title = '缅甸全国省级报道事件规则烈度地图';
    } catch (error) {
        if (seq !== homeSequence) return;
        homeMessage('home-map','地图暂不可用，风险摘要与事件记录仍可查看。' + (error.name === 'AbortError' ? '请求超时。' : error.message),
            error.message.includes('版本') || error.message.includes('修订') ? loadHomeSnapshot : () => loadHomeMap(snapshot,seq));
    } finally { clearTimeout(timer); }
}
async function loadHomeSnapshot() {
    const seq = ++homeSequence;
    homeSnapshot = null;
    document.getElementById('home-range').textContent = `近30个业务日 · 截至 ${homeDay()} · 缅甸全国 · 正在读取…`;
    if (homeMapController) homeMapController.abort();
    ['home-risk','home-data','home-events','home-map'].forEach(id => homeMessage(id,'正在读取已保存观测…'));
    for (const [id,path] of [['home-risk-link','/dashboard/indicators'],['home-data-link','/dashboard/data'],['home-events-link','/dashboard/events'],['home-map-link','/map']]) document.getElementById(id).href = path;
    try {
        const requested = {days:30,end_date:homeDay(),region:'MMR'};
        const result = await fetchJSON('/api/report?' + viewQuery({format:'json'},requested),{},30000);
        if (seq !== homeSequence) return;
        if (!result.success) throw Error(result.error || '观测快照不可用');
        const filters = result.data?.filters;
        if (!filters || Number(filters.days)!==30 || filters.region!=='MMR' || filters.end_date!==requested.end_date || filters.source) throw Error('默认观测范围不一致，未展示其他筛选结果。');
        if (typeof result.data.revision!=='string' || !result.data.revision) throw Error('观测快照缺少修订，无法展示同版地图。');
        homeSnapshot = result.data;
        renderHomeSnapshot(homeSnapshot);
        loadHomeMap(homeSnapshot,seq);
    } catch (error) {
        if (seq !== homeSequence) return;
        homeSnapshot = null;
        ['home-risk','home-data','home-events'].forEach(id => homeMessage(id,'观测快照暂不可用；未用旧值回填。',loadHomeSnapshot));
        homeMessage('home-map','尚无同一修订的观测快照，地图未加载。');
    }
}
document.addEventListener('DOMContentLoaded',() => { loadHomeSnapshot(); loadHomeSources(); });
