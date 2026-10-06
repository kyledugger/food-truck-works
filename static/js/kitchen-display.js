(() => {
  'use strict';
  const feed=document.getElementById('store-order-feed'),grid=document.getElementById('live-stores');
  if(feed?.dataset.kitchen!=='true'||!grid?.dataset.storeId)return;
  const base='/dashboard/stores/'+grid.dataset.storeId+'/kitchen';
  const recent=document.getElementById('kitchen-recent-feed'),status=document.getElementById('kitchen-status');
  let source=null,loading=false,queued=false,stopped=false,pending=false,available=false,live=false,fingerprint='',controller=null;
  let serverTime=Date.now(),observed=performance.now(),snapshot=null;
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const states={available:'○ Available',claimed:'◐ In progress',done:'✓ Done'};
  function buttonsEnabled(){document.querySelectorAll('[data-kitchen-action]').forEach(b=>{b.disabled=!available||pending;});}
  function tick(){
    const now=serverTime+performance.now()-observed;
    document.querySelectorAll('[data-order-created]').forEach(node=>{
      const end=node.dataset.orderReady?Date.parse(node.dataset.orderReady):now;
      const seconds=Math.max(0,Math.floor((end-Date.parse(node.dataset.orderCreated))/1000));
      const minutes=Math.floor(seconds/60);
      node.textContent=(node.dataset.orderReady?'Ready in ':'Elapsed ')+(minutes>=60?Math.floor(minutes/60)+'h '+minutes%60+'m':minutes+'m '+String(seconds%60).padStart(2,'0')+'s');
    });
  }
  function button(label,ticket,action,key=null){
    const b=el('button','button is-small kitchen-action',label);b.type='button';b.dataset.kitchenAction=action;
    b.setAttribute('aria-label',label+(key?' item in order ':' ')+ticket.number);
    b.addEventListener('click',()=>act(ticket,action,key));return b;
  }
  function card(ticket,finished=false){
    const root=el('article','store-order kitchen-ticket'),head=el('div','store-order-head');
    const timer=el('strong','kitchen-elapsed');timer.dataset.orderCreated=ticket.created_at;
    if(finished)timer.dataset.orderReady=ticket.ready_at;
    head.append(el('strong','','Order '+ticket.number),timer);root.append(head);
    const controls=el('div','kitchen-order-actions');
    if(finished){controls.append(el('span','kitchen-state kitchen-done','✓ Ready'),button('Undo order',ticket,'undo'));}
    else{
      if(ticket.items.some(i=>i.state==='available'))controls.append(button('Claim available',ticket,'claim'));
      controls.append(button('Complete order',ticket,'done'));
    }
    root.append(controls);
    const table=el('table');table.setAttribute('aria-label','Order '+ticket.number+' preparation');
    const header=el('tr');['Item','Qty','Preparation'].forEach(t=>header.append(el('th','',t)));
    const thead=el('thead');thead.append(header);table.append(thead);
    const body=el('tbody');
    for(const item of ticket.items){
      const row=el('tr','kitchen-'+item.state),name=el('td','kitchen-item-name',item.name);
      if(item.sku)name.append(el('small','kitchen-sku',item.sku));
      const actions=el('td','kitchen-item-actions');actions.append(el('span','kitchen-state',states[item.state]||states.available));
      if(!finished){
        if(item.state==='available')actions.append(button('Claim',ticket,'claim',item.key));
        if(item.state==='claimed')actions.append(button('Done',ticket,'done',item.key),button('Release',ticket,'release',item.key));
        if(item.state==='done')actions.append(button('Undo',ticket,'undo',item.key));
      }
      row.append(name,el('td','',item.quantity),actions);body.append(row);
    }
    table.append(body);root.append(table);return root;
  }
  function render(data){
    serverTime=Date.parse(data.generated_at);observed=performance.now();snapshot=data;
    const next=JSON.stringify([data.active,data.recent]);
    if(next!==fingerprint){
      fingerprint=next;const container=feed.closest('.store-orders'),scroll=container.scrollTop;
      feed.replaceChildren(...data.active.map(t=>card(t)));recent.replaceChildren(...data.recent.map(t=>card(t,true)));
      if(!data.active.length)feed.append(el('p','live-empty','No unfinished orders.'));
      document.getElementById('kitchen-count').textContent=data.active.length+' active';
      document.getElementById('kitchen-recent-count').textContent='('+data.recent.length+')';
      container.scrollTop=scroll;
    }
    tick();buttonsEnabled();
  }
  function revoke(){
    stopped=true;available=false;source?.close();source=null;controller?.abort();
    feed.replaceChildren();recent.replaceChildren();snapshot=null;fingerprint='';
    document.getElementById('kitchen-count').textContent='';document.getElementById('kitchen-recent-count').textContent='';
    status.textContent='Kitchen access changed. Sign in again.';
  }
  async function refresh(){
    if(stopped||document.hidden)return;
    if(loading){queued=true;return;}
    loading=true;controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetch(base,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(response.status===401||response.status===403){revoke();document.dispatchEvent(new Event('store-access-denied'));return;}
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Queue could not refresh.');
      available=true;render(data);status.textContent=live?'Live kitchen updates':'Kitchen updates · polling fallback every 5 seconds';
    }catch(error){if(!stopped){available=false;buttonsEnabled();status.textContent=(error.name==='AbortError'?'Queue refresh timed out.':error.message)+' Actions paused until the queue reconnects.';}}
    finally{clearTimeout(timeout);controller=null;loading=false;if(queued){queued=false;refresh();}}
  }
  async function act(ticket,action,key){
    if(pending||!available||stopped)return;
    if(action==='done'&&!key&&ticket.items.some(i=>i.state==='available')&&!window.confirm('Mark every remaining item in order '+ticket.number+' ready? Some items have not been claimed.'))return;
    pending=true;buttonsEnabled();let message='';
    try{
      const response=await fetch(base+'/'+ticket.id,{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Kitchen-CSRF':feed.dataset.csrf},
        body:JSON.stringify({action,revision:ticket.revision,item_key:key}),signal:AbortSignal.timeout(10000)});
      if(response.status===401||response.status===403){revoke();document.dispatchEvent(new Event('store-access-denied'));return;}
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Action could not be saved.');
      document.dispatchEvent(new Event('kitchen-queue-changed'));
    }catch(error){message=error.name==='TimeoutError'?'Save response timed out. Reviewing the saved queue before another tap.':error.message;}
    finally{await refresh();pending=false;buttonsEnabled();if(message)status.textContent=message;}
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
