/* 私密工作区：不使用localStorage；身份变动及页面离开清除内容。 */
(() => {
    const el=id=>document.getElementById(id);
    let uid=null, cid=null, offset=0, epoch=0, canWrite=false, ready=false, historyEpoch=0, pendingSubmission=null;
    const status=text=>{el('workspace-status').textContent=text;};
    function add(parent,tag,text){const n=document.createElement(tag); n.textContent=text||''; parent.append(n); return n;}
    function clear(){epoch++; historyEpoch++; uid=null; cid=null; ready=false; pendingSubmission=null; el('preferences-form').reset(); el('workspace-user').textContent=''; el('workspace-messages').replaceChildren(); el('workspace-history').replaceChildren(); el('workspace-input').value=''; el('workspace-title').textContent='请选择会话'; el('workspace-export').hidden=true; document.querySelectorAll('main button, main textarea').forEach(n=>n.disabled=true);}
    window.addEventListener('pagehide',clear);
    window.addEventListener('pageshow',event=>{if(event.persisted) location.reload();});
    async function api(path,method='GET',body,key){
        const auth=await fetchJSON('/api/session');
        if(!auth.data.user || auth.data.user.id!==uid){clear(); throw new Error('登录状态已变化，请刷新或重新登录');}
        const identity=uid;
        const options={method,headers:{'Content-Type':'application/json','X-Workspace-User':identity}};
        if(body!==undefined) options.body=JSON.stringify(body);
        if(key) options.headers['Idempotency-Key']=key;
        const response=await fetchJSON('/api/workspace'+path,options,30000);
        const after=(await fetchJSON('/api/session')).data.user;
        if(!after || after.id!==identity || uid!==identity){clear();throw new Error('账户已变化，已清除私密内容');}
        if(!response.success) throw new Error(response.error||'请求失败');
        return response.data;
    }
    const safely=fn=>async(...args)=>{try{await fn(...args);}catch(error){status(error.message);}};
    async function history(reset=true){
        const seq=++historyEpoch;
        if(reset){offset=0; el('workspace-history').replaceChildren();}
        const rows=await api('/conversations?offset='+offset);
        if(seq!==historyEpoch)return;
        for(const row of rows){const b=add(el('workspace-history'),'button',row.title); b.className='btn'; b.onclick=safely(()=>open(row.id));}
        offset+=rows.length; el('workspace-more').disabled=rows.length<20;
    }
    function evidence(parent,payload){
        if(payload.context_truncated) add(parent,'p','上下文超过预算，已截断较早轮次。');
        add(parent,'p',payload.disclaimer||payload.reason||'');
        const snapshot=payload.evidence||{};
        add(parent,'p',`证据截止日：${snapshot.end_date||'—'}；评分版本：${snapshot.algorithm_version||'—'}`);
        const details=add(parent,'details'); add(details,'summary','展开引用证据');
        for(const item of snapshot.records||[]){
            add(details,'p',`[${item.id}] ${item.title||''} ${item.date||''}`);
            add(details,'p',item.text);
            if(item.url){try{const url=new URL(item.url,location.origin); if(['http:','https:'].includes(url.protocol)) {const a=add(details,'a','查看来源'); a.href=url.href; a.target='_blank'; a.rel='noopener noreferrer';}}catch(_){} }
        }
    }
    function feedback(parent,message,existing){
        if(!canWrite || message.payload.status!=='ok') return;
        const form=add(parent,'form'); add(form,'label','您对这个回答满意吗？');
        const rating=add(form,'select'); rating.setAttribute('aria-label','满意程度');
        for(let i=1;i<=5;i++) rating.add(new Option(i+'分',i)); rating.value=existing?.rating||5;
        const tag=add(form,'select'); tag.setAttribute('aria-label','反馈原因');
        [['','选择原因'],['helpful','有帮助'],['unclear','不清楚'],['wrong','有错误'],['missing_evidence','证据不足']].forEach(([v,t])=>tag.add(new Option(t,v))); tag.value=existing?.tag||'';
        const comment=add(form,'input'); comment.placeholder='可选意见，最多1000字'; comment.maxLength=1000; comment.value=existing?.comment||'';
        add(form,'button','保存评价'); const remove=add(form,'button','撤回评价'); remove.type='button';
        form.onsubmit=safely(async event=>{event.preventDefault(); await api('/messages/'+message.id+'/feedback','PUT',{rating:Number(rating.value),tag:tag.value,comment:comment.value});status('评价已保存，可修改或撤回。');});
        remove.onclick=safely(async()=>{await api('/messages/'+message.id+'/feedback','DELETE');comment.value='';tag.value='';status('评价已撤回');});
    }
    async function open(id){
        const seq=++epoch; cid=id; el('workspace-messages').replaceChildren(); status('加载会话…');
        const conversation=await api('/conversations/'+id);
        if(seq!==epoch)return;
        el('workspace-title').textContent=conversation.title;
        for(const message of conversation.messages){
            const card=add(el('workspace-messages'),'article');card.className='workspace-message '+message.role;
            add(card,'h3',message.role==='user'?'我':'研究助手');
            const labels={observed_changes:'观测变化',contributions:'指标贡献',possible_explanations:'可能解释',recommendations:'条件化建议',data_gaps:'数据缺口'};
            if(message.payload.answer){for(const [key,items] of Object.entries(message.payload.answer)){add(card,'h4',labels[key]||key);for(const item of items)add(card,'p',item.text+' ['+item.evidence_ids.join(', ')+']');}}
            else add(card,'p',message.content);
            if(message.role==='assistant'){evidence(card,message.payload);feedback(card,message,conversation.feedback.find(f=>f.message_id===message.id));}
        }
        const pending=conversation.requests.filter(r=>['queued','running'].includes(r.status));
        el('workspace-input').disabled=!canWrite; el('workspace-send').disabled=!canWrite||pending.length>0;
        el('workspace-rename').disabled=el('workspace-delete').disabled=!canWrite;
        el('workspace-export').href='/api/workspace/conversations/'+id+'/export';el('workspace-export').hidden=false;
        status(pending.length?'任务等待中；如长时间无变化，请检查独立worker。':'会话已加载；数据仅本人可见。');
        if(pending.length) poll(pending[0].id,id,seq);
    }
    async function poll(rid,id,seq){
        try{
            for(let i=0;i<30&&seq===epoch;i++){
                const task=await api('/requests/'+rid);
                if(seq!==epoch)return;
                if(['done','failed'].includes(task.status)){
                    if(task.status==='failed'){status(task.result.reason||'任务失败，请重新提问');el('workspace-send').disabled=!canWrite;return;}
                    await open(id);return;
                }
                await new Promise(resolve=>setTimeout(resolve,2000));
            }
            if(seq===epoch){status('仍在等待任务；可重新打开会话检查，未自动重复提交。');}
        }catch(error){if(seq===epoch)status(error.message);}
    }
    el('workspace-new').onclick=safely(async()=>{const data=await api('/conversations','POST',{title:'新对话'});await history();await open(data.id);});
    el('workspace-more').onclick=safely(()=>history(false));
    el('workspace-rename').onclick=safely(async()=>{if(!cid)return; const title=prompt('会话标题',el('workspace-title').textContent); if(title){await api('/conversations/'+cid,'PATCH',{title}); await history();await open(cid);}});
    el('workspace-delete').onclick=safely(async()=>{if(!cid||!confirm('删除该会话、消息及反馈？任务不会重新写回。'))return;await api('/conversations/'+cid,'DELETE');cid=null;epoch++;el('workspace-messages').replaceChildren();el('workspace-export').hidden=true;await history();status('会话已删除');});
    el('workspace-form').onsubmit=safely(async event=>{
        event.preventDefault();if(!cid)return;
        const query=readViewFilters(); if(query.source)throw new Error('AI研判暂不支持单来源筛选，请清空来源。');
        el('workspace-send').disabled=true;
        const id=cid, seq=epoch;
        const body={question:el('workspace-input').value,days:Number(query.days),end_date:query.end_date,region:query.region};
        const signature=JSON.stringify([id,body]);
        if(!pendingSubmission || pendingSubmission.signature!==signature)pendingSubmission={signature,key:crypto.randomUUID()};
        try{await api('/conversations/'+id+'/messages','POST',body,pendingSubmission.key);pendingSubmission=null;if(cid===id&&seq===epoch){el('workspace-input').value='';await open(id);}}
        catch(error){el('workspace-send').disabled=false;throw error;}
    });
    function showPreferences(p){el('preference-region').value=p.region;el('preference-scenario').value=p.scenario;el('preference-verbosity').value=p.verbosity;el('preference-suggestions').checked=p.suggestions;}
    el('preferences-form').onsubmit=safely(async event=>{event.preventDefault();const p={region:el('preference-region').value,scenario:el('preference-scenario').value,verbosity:el('preference-verbosity').value,suggestions:el('preference-suggestions').checked};showPreferences(await api('/preferences','PUT',p));status('偏好已保存');});
    el('preferences-clear').onclick=safely(async()=>{showPreferences(await api('/preferences','PUT',{}));status('已恢复默认偏好，功能推荐已关闭');});
    el('workspace-logout').onclick=safely(async()=>{clear();await fetchJSON('/api/logout',{method:'POST'});location.href='/login';});
    window.addEventListener('focus',async()=>{if(!uid)return;try{const current=(await fetchJSON('/api/session')).data.user;if(!current||current.id!==uid){clear();status('账户已变化，请重新登录');}}catch(_){clear();status('无法验证账户，请刷新');}});
    (async()=>{
        initViewFilters();
        try{
            const auth=(await fetchJSON('/api/session')).data;
            if(!auth.shared_mode||!auth.user)throw new Error('个人工作区需要共享模式账户；管理员建号后登录，本机匿名模式不保存个人对话。');
            uid=auth.user.id;canWrite=['analyst','admin'].includes(auth.user.role);el('workspace-user').textContent=auth.user.username+' / '+auth.user.role;
            await history();showPreferences(await api('/preferences'));ready=true;
            el('workspace-new').disabled=!canWrite;el('preferences-save').disabled=el('preferences-clear').disabled=false;
            status(canWrite?'请选择或新建会话。':'当前为只读账户，可管理偏好；AI对话需要analyst权限。');
            const q=new URLSearchParams(location.search); if(q.get('question'))el('workspace-input').value=q.get('question').slice(0,2000);
        }catch(error){status(error.message);}
    })();
})();
