/* 月/年度面板只读呈现；不触发采集、基准拟合或后台生成。 */
(() => {
    const root = document.getElementById('risk-panel-section');
    if (!root) return;
    const el = id => document.getElementById(id);
    let sequence = 0;
    const names = {conflict_frequency:'冲突频次', event_severity:'事件严重度', china_myanmar_relation:'中缅关系',
        neighbor_relation:'周边邻国关系',western_relation:'西方关系',china_us_rivalry:'中美博弈',
        nightlight_change:'夜光变化',displacement:'流离失所',intl_sanction:'国际制裁'};
    const quality = {observed:'原始观测',derived:'观测派生',proxy:'明确代理',missing:'缺测'};
    const states = {ok:'九项可用',blocked:'正式分未合成',partial_proxy:'部分指标代理',ambiguous:'版本需选择',not_computed:'尚未计算'};
    function add(parent,tag,text) {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;parent.append(n);return n;}
    function numeric(value) {return typeof value==='number'&&Number.isFinite(value)?value.toFixed(2):'无数据';}
    async function load() {
        const seq=++sequence;
        const area=el('risk-panel-body');area.replaceChildren();add(area,'p','正在读取已保存的月/年度面板…');
        const filters=readViewFilters();
        if(filters.source){area.textContent='多源面板不套用单新闻来源筛选，请清空来源。';return;}
        const frequency=el('panel-frequency').value;
        const query=new URLSearchParams({frequency,count:frequency==='annual'?5:12,mode:el('panel-mode').value,region:filters.region,end_date:filters.end_date});
        if(el('panel-calibration').value)query.set('calibration_id',el('panel-calibration').value);
        try {
            const result=(await fetchJSON('/api/risk_panels?'+query)).data;
            if(seq!==sequence)return;
            area.replaceChildren();add(area,'p',result.note);
            const options=el('panel-calibration'), selected=options.value;
            options.replaceChildren(new Option('自动（有冲突时须选择）',''));
            result.calibrations.filter(c=>c.frequency===frequency&&c.mode===el('panel-mode').value).forEach(c=>options.add(new Option(c.description+' · '+c.id.slice(0,8),c.id)));
            options.value=selected;
            const wrapper=add(area,'div');wrapper.className='observation-table';
            const table=add(wrapper,'table'),head=add(table,'thead'),hr=add(head,'tr');
            ['周期（结束排他）','风险分／100','指标权重覆盖','状态与证据'].forEach(t=>add(hr,'th',t));
            const body=add(table,'tbody');
            for(const p of result.panels){
                const tr=add(body,'tr');add(tr,'td',p.period_start+' → '+p.period_end);
                const score=add(tr,'td',numeric(p.score));if(p.score==null)score.className='panel-missing';
                add(tr,'td',typeof p.coverage==='number'?(p.coverage*100).toFixed(1)+'%':'—');
                const cell=add(tr,'td');add(cell,'p',states[p.status]||p.status);
                if(p.reason)add(cell,'p',p.reason);
                const details=add(cell,'details');add(details,'summary','指标、来源与计算条件');
                add(details,'p',p.label||'当前周期没有已保存计算结果');
                for(const [key,item] of Object.entries(p.indicators||{})){
                    add(details,'p',`${names[key]||key}：原值 ${numeric(item.raw)} ${item.unit||''}；${item.source||'来源未到位'} / ${quality[item.quality]||item.quality}；贡献 ${numeric(item.contribution_points)}分`);
                    if(item.reason)add(details,'p',item.reason);
                }
                add(details,'p','参数版本：'+(p.parameters?.approved_version||'未审定')+'；基准：'+(p.calibration_id?.slice(0,12)||'未配置'));
            }
            const ids=add(area,'details');add(ids,'summary','面板修订与算法版本');add(ids,'p',result.revision+' · '+result.algorithm_version);
            const sources=(await fetchJSON('/api/risk_sources/status')).data;
            if(seq!==sequence)return;
            const sourceDetails=add(area,'details');add(sourceDetails,'summary','数据源分工与接入状态');
            for(const [source,s] of Object.entries(sources))add(sourceDetails,'p',source.toUpperCase()+'：'+s.reason+'；'+(s.batches?.length||0)+'个已导入批次');
        } catch(error){if(seq===sequence){area.replaceChildren();add(area,'p',error.message);}}
    }
    root.addEventListener('toggle',()=>{if(root.open)load();else sequence++;});
    el('panel-refresh').onclick=load;
    // 切换周期/模式时旧基准必然不匹配，先清空再读，避免误报“尚未计算”。
    ['panel-frequency','panel-mode'].forEach(id=>el(id).addEventListener('change',()=>{el('panel-calibration').value='';load();}));
    el('panel-calibration').addEventListener('change',load);
    document.querySelectorAll('.view-filters input,.view-filters select').forEach(n=>n.addEventListener('change',()=>{sequence++;el('risk-panel-body').textContent='筛选已改变，请刷新多源面板。';}));
})();
