(() => {
  'use strict';
  const feed=document.getElementById('store-order-feed'),grid=document.getElementById('live-stores');
  const storeId=feed?.dataset.storeId||grid?.dataset.storeId,board=!!document.querySelector('[data-kitchen-board]');
  if(feed?.dataset.kitchen!=='true'||!storeId)return;
  const base='/dashboard/stores/'+storeId+(feed.dataset.test==='true'?'/kitchen-test':'/kitchen');
  const recent=document.getElementById('kitchen-recent-feed'),status=document.getElementById('kitchen-status');
  let source=null,loading=false,stopped=false,available=false,live=false,fingerprint='',controller=null,refreshTask=null;
  const pending=new Map();
  let timerProfile={green_seconds:0,yellow_seconds:300,red_seconds:600};
  let serverTime=Date.now(),observed=performance.now(),snapshot=null;
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  function buttonsEnabled(){document.querySelectorAll('[data-kitchen-action]').forEach(b=>{const disabled=!available||pending.has(Number(b.dataset.ticketId))||b.dataset.blocked==='true';if(b.tagName==='BUTTON')b.disabled=disabled;else b.setAttribute('aria-disabled',String(disabled));});}
  function tick(){
    const now=serverTime+performance.now()-observed;
    document.querySelectorAll('[data-order-created]').forEach(node=>{
      const end=node.dataset.orderReady?Date.parse(node.dataset.orderReady):now;
      const seconds=Math.max(0,Math.floor((end-Date.parse(node.dataset.orderCreated))/1000));
      const minutes=Math.floor(seconds/60);
      const band=seconds>=timerProfile.red_seconds?'red':seconds>=timerProfile.yellow_seconds?'yellow':seconds>=timerProfile.green_seconds?'green':'neutral';
      const symbol={green:'●',yellow:'▲',red:'! ',neutral:''}[band];
      node.textContent=(node.dataset.held==='true'?'Ⅱ ':symbol+' ')+(minutes>=60?Math.floor(minutes/60)+'h '+minutes%60+'m':minutes+'m '+String(seconds%60).padStart(2,'0')+'s');
      node.dataset.ageBand=band;
      node.setAttribute('aria-label',(node.dataset.orderReady?'Ready after ':node.dataset.held==='true'?'Resume order '+node.dataset.orderNumber+'; age ':'Hold order '+node.dataset.orderNumber+'; age ')+minutes+' minutes '+seconds%60+' seconds; '+band+' timer band');
    });
  }
  function button(label,ticket,action,key=null){
    const b=el('button','button is-small kitchen-action',label);b.type='button';b.dataset.kitchenAction=action;b.dataset.ticketId=ticket.id;
    b.setAttribute('aria-label',label+(key?' item in order ':' ')+ticket.number);
    b.addEventListener('click',()=>act(ticket,action,key));return b;
  }
  function card(ticket,finished=false){
    const held=ticket.state==='held';
    const root=el('article','store-order kitchen-ticket'+(held?' ticket-held':finished?' ticket-ready':ticket.items.some(i=>i.state==='claimed')?' ticket-in-progress':'')),head=el('div','store-order-head');
    const title=el(finished?'strong':'button','kitchen-complete-header',(held?'Ⅱ Held · ':'')+(ticket.customer_name||ticket.notes||''));
    if(!finished){title.type='button';title.dataset.kitchenAction='done';title.dataset.ticketId=ticket.id;title.dataset.blocked=String(held);title.setAttribute('aria-label','Complete order '+ticket.number);title.title=held?'Resume using the timer before completing':'Tap header to complete this order';title.addEventListener('click',event=>{event.stopPropagation();act(ticket,'done',null);});head.addEventListener('click',event=>{if(event.target===head)act(ticket,'done',null);});}
    const timer=el(finished?'strong':'button','kitchen-elapsed');timer.dataset.orderCreated=ticket.created_at;timer.dataset.held=String(held);timer.dataset.orderNumber=ticket.number;
    if(!finished){timer.type='button';timer.dataset.kitchenAction=held?'resume':'hold';timer.dataset.ticketId=ticket.id;timer.title=held?'Resume preparation':'Hold preparation; order age keeps counting';timer.addEventListener('click',event=>{event.stopPropagation();act(ticket,held?'resume':'hold',null);});}
    if(finished)timer.dataset.orderReady=ticket.ready_at;
    head.append(title,timer);root.append(head);
    if(ticket.notes&&ticket.customer_name)root.append(el('p','kitchen-callout','Order note: '+ticket.notes));
    if(finished){const controls=el('div','kitchen-order-actions');controls.append(el('span','kitchen-state kitchen-done','✓ Ready'),button('Undo order',ticket,'undo'));root.append(controls);}
    const table=el('table');table.setAttribute('aria-label','Order '+ticket.number+' preparation');
    const body=el('tbody');
    for(const item of ticket.items){
      const row=el('tr','kitchen-'+item.state),name=el('td','kitchen-item-name',item.quantity+' × '+item.name);
      for(const group of item.modifiers||[])name.append(el('div','kitchen-modifier',group.attribute.replaceAll('_',' ')+': '+group.values.map(value=>value.replaceAll('_',' ')).join(', ')));
      if(item.saving){row.classList.add('kitchen-saving');name.append(el('span','kitchen-save-indicator',' · Saving…'));row.setAttribute('aria-busy','true');}
      if(!finished){
        const action=item.state==='available'?'claim':item.state==='claimed'?'done':'undo';
        const label={claim:'Claim',done:'Done',undo:'Undo'}[action];
        row.setAttribute('role','button');row.tabIndex=0;row.dataset.kitchenAction=action;row.dataset.ticketId=ticket.id;row.dataset.itemKey=item.key;
        row.setAttribute('aria-label',label+' item in order '+ticket.number);
        row.dataset.blocked=String(held);row.setAttribute('aria-disabled',String(!available||pending.has(ticket.id)||held));
        row.addEventListener('click',()=>act(ticket,action,item.key));
        row.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();act(ticket,action,item.key);}});
      }
      row.append(name);body.append(row);
    }
    table.append(body);root.append(table);return root;
  }
  function render(data,local=false){
    document.dispatchEvent(new CustomEvent('kitchen-flow',{detail:data.flow}));
    if(!local){serverTime=Date.parse(data.generated_at);observed=performance.now();snapshot=data;}
    if(data.timer_profile){timerProfile=data.timer_profile;document.dispatchEvent(new CustomEvent('kitchen-timer-profile',{detail:timerProfile}));}
    const active=data.active.map(ticket=>{
      const op=pending.get(ticket.id);if(!op)return ticket;
      const holdAction=op.action==='hold'||op.action==='resume';
      return {...ticket,state:holdAction&&ticket.revision===op.revision?(op.action==='hold'?'held':'active'):ticket.state,items:ticket.items.map(item=>{
        if(holdAction)return item;
        if(op.key&&item.key!==op.key)return item;
        const optimistic=ticket.revision===op.revision;
        const state=optimistic?(op.action==='claim'&&item.state==='available'?'claimed':op.action==='done'?'done':op.action==='undo'||op.action==='release'?'available':item.state):item.state;
        return {...item,state,saving:true};
      })};
    });
    const next=JSON.stringify([active,data.recent]);
    if(next!==fingerprint){
      fingerprint=next;const container=feed.closest('.store-orders'),scroll=container.scrollTop,left=container.scrollLeft;
      const focus=document.activeElement,focusTicket=focus?.dataset.ticketId,focusKey=focus?.dataset.itemKey;
      feed.replaceChildren(...active.map(t=>card(t)));recent.replaceChildren(...data.recent.map(t=>card(t,true)));
      if(!data.active.length)feed.append(el('p','live-empty','No unfinished orders.'));
      document.getElementById('kitchen-count').textContent=data.active.length+' active';
      document.getElementById('kitchen-recent-count').textContent='('+data.recent.length+')';
      container.scrollTop=scroll;
      container.scrollLeft=left;
      if(focusTicket&&focusKey)feed.querySelector('[data-ticket-id="'+focusTicket+'"][data-item-key="'+focusKey+'"]')?.focus({preventScroll:true});
    }
    tick();buttonsEnabled();
  }
  function revoke(){
    stopped=true;available=false;source?.close();source=null;controller?.abort();
    feed.replaceChildren();recent.replaceChildren();snapshot=null;fingerprint='';
    document.getElementById('kitchen-count').textContent='';document.getElementById('kitchen-recent-count').textContent='';
    status.textContent='Kitchen access changed. Sign in again.';
  }
  async function refresh(force=false){
    if(stopped||document.hidden)return;
    if(loading){await refreshTask;if(force===true)return refresh(true);return;}
    refreshTask=load();await refreshTask;
  }
  async function load(){
    loading=true;controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetch(base,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(response.status===401||response.status===403){revoke();document.dispatchEvent(new Event('store-access-denied'));return;}
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Queue could not refresh.');
      available=true;render(data);status.textContent=live?(feed.dataset.test==='true'?'Shared test kitchen updates':'Live kitchen updates'):'Kitchen updates · polling fallback every 5 seconds';
    }catch(error){if(!stopped){available=false;document.dispatchEvent(new Event('kitchen-flow-unavailable'));buttonsEnabled();status.textContent=(error.name==='AbortError'?'Queue refresh timed out.':error.message)+' Actions paused until the queue reconnects.';}}
    finally{clearTimeout(timeout);controller=null;loading=false;}
  }
  async function act(ticket,action,key){
    if(pending.has(ticket.id)||!available||stopped)return;
    if(ticket.state==='held'&&action!=='resume')return;
    pending.set(ticket.id,{action,key,revision:ticket.revision});if(snapshot)render(snapshot,true);buttonsEnabled();let message='';
    try{
      const response=await fetch(base+'/'+ticket.id,{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Kitchen-CSRF':feed.dataset.csrf},
        body:JSON.stringify({action,revision:ticket.revision,item_key:key}),signal:AbortSignal.timeout(10000)});
      if(response.status===401||response.status===403){revoke();document.dispatchEvent(new Event('store-access-denied'));return;}
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Action could not be saved.');
      document.dispatchEvent(new Event('kitchen-queue-changed'));
    }catch(error){message=error.name==='TimeoutError'?'Save response timed out. Reviewing the saved queue before another tap.':error.message;}
    finally{await refresh(true);pending.delete(ticket.id);if(snapshot&&!stopped)render(snapshot,true);buttonsEnabled();if(message&&!stopped)status.textContent='Order '+ticket.number+': '+message;}
  }
  function connect(){
    if(stopped||document.hidden||source||!window.EventSource)return;
    source=new EventSource(base+'/events');
    source.onopen=()=>{live=true;refresh();};
    source.onerror=()=>{live=false;if(available)status.textContent='Live connection interrupted · polling fallback every 5 seconds';};
    source.addEventListener('queue-changed',()=>{refresh();document.dispatchEvent(new Event('kitchen-queue-changed'));});
    source.addEventListener('access-denied',()=>{revoke();document.dispatchEvent(new Event('store-access-denied'));});
  }
  document.addEventListener('store-access-denied',revoke);
  document.addEventListener('visibilitychange',()=>{if(document.hidden){source?.close();source=null;live=false;}else{refresh();connect();}});
  document.getElementById('live-refresh')?.addEventListener('click',refresh);
  document.addEventListener('store-data',refresh);
  window.addEventListener('pagehide',()=>{stopped=true;source?.close();controller?.abort();feed.replaceChildren();recent.replaceChildren();});
  setInterval(()=>{if(!document.hidden)tick();},1000);
  setInterval(refresh,5000);refresh();connect();
})();
