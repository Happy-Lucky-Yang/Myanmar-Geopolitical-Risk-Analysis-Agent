/* 仪表盘AI区独立加载；失败不影响风险、地图和事件卡片。 */
(() => {
    const area=document.getElementById('ai-briefing-body'), button=document.getElementById('ai-briefing-request');
    if (!area || !button) return;
    let generation=0, submission=null, requesting=false;
    function show(text){area.textContent=text;}
    function render(result){
        area.replaceChildren();
        const p=text=>{const n=document.createElement('p');n.textContent=text;area.append(n);};
        p(result.reason||result.disclaimer||'');
        const ev=result.evidence||{};p(`证据截止：${ev.end_date||'—'}；地区：${ev.region||'—'}；版本：${ev.algorithm_version||'—'}（不代表今天）`);
        const labels={observed_changes:'观测变化',contributions:'指标贡献',possible_explanations:'可能解释',recommendations:'条件化建议',data_gaps:'数据缺口'};
        for(const [key,items] of Object.entries(result.answer||{})){p(labels[key]||key);items.forEach(item=>p(item.text+' ['+item.evidence_ids.join(', ')+']'));}
        for(const item of ev.records||[]){p(`[${item.id}] ${item.title} ${item.date||''}`);p(item.text);if(item.url){try{const url=new URL(item.url,location.origin);if(['http:','https:'].includes(url.protocol)){const a=document.createElement('a');a.textContent='查看来源';a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';area.append(a);}}catch(_){}}}
    }
    document.querySelectorAll('.view-filters input,.view-filters select').forEach(n=>n.addEventListener('change',()=>{generation++;show('筛选已变化，请重新请求解释。');}));
    button.onclick=async()=>{
        if(requesting)return;
        requesting=true;button.disabled=true;
        const epoch=++generation, filters=readViewFilters();
        const signature=JSON.stringify(filters);
        if(!submission||submission.signature!==signature)submission={signature,key:crypto.randomUUID()};
        if(filters.source){requesting=false;button.disabled=false;show('AI综合解释暂不支持单来源，请清空来源。');return;}
        const permission=await modelPermission('analyst');
        if(!permission.allowed||!permission.auth?.shared_mode){requesting=false;show(permission.reason||'证据型研判需要共享账户和独立worker。');button.disabled=true;return;}
        show('正在提交任务…');
        try{
            const user=(await fetchJSON('/api/session')).data.user;
            if(!user)throw new Error('请先登录');
            const task=await fetchJSON('/api/workspace/briefings',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':submission.key,'X-Workspace-User':user.id},body:JSON.stringify({question:'请根据证据解释所选窗口风险变化，并给出附条件的建议。',days:Number(filters.days),end_date:filters.end_date,region:filters.region})},30000);
            for(let i=0;i<30&&epoch===generation;i++){
                const response=await fetchJSON('/api/workspace/requests/'+task.data.request_id);
                if(epoch!==generation)return;
                const current=(await fetchJSON('/api/session')).data.user;
                if(!current||current.id!==user.id)throw new Error('账户已变化，请刷新页面');
                if(['done','failed'].includes(response.data.status)){submission=null;render(response.data.result);return;}
                show('已排队，等待独立worker；没有运行worker时不会产生回答。');
                await new Promise(resolve=>setTimeout(resolve,2000));
            }
            if(epoch===generation)show('任务仍在等待。请检查worker；不要反复提交。');
        }catch(error){if(epoch===generation)show(error.message);}finally{requesting=false;await loadEligibility(false);}
    };
    async function loadEligibility(readLatest=true){
        button.disabled=true;
        try{
            const flags=(await fetchJSON('/api/product/status')).data;
            if(!flags.workspace||!flags.risk_briefing){show('AI研判默认关闭；由管理员完成共享账户、迁移与worker配置后启用。');return;}
            const permission=await modelPermission('analyst');
            if(!permission.allowed||!permission.auth?.shared_mode){generation++;show(permission.reason||'证据型研判需要共享账户、PostgreSQL和独立worker。');return;}
            button.disabled=requesting;
            if(!readLatest||requesting)return;
            const epoch=generation;
            const response=await fetchJSON('/api/workspace/briefings/latest');
            if(epoch!==generation)return;
            const ev=response.data?.result?.evidence, filters=readViewFilters();
            if(ev && !filters.source && ev.region===filters.region && ev.end_date===filters.end_date && ev.days===Number(filters.days))render(response.data.result);
            else show('当前筛选暂无匹配的自动研判；未满足证据门槛时不会生成。可以主动请求历史窗口解释。');
        }catch(error){button.disabled=true;show(error.message);}
    }
    loadEligibility();
    window.addEventListener('focus',()=>loadEligibility(!requesting));
})();
