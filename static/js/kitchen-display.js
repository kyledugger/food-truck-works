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
  const itemIdentity=item=>JSON.stringify([item?.key,item?.name,item?.quantity,item?.modifiers||[]]);
  const soundButton=el('button',board?'button':'button is-small is-light','♫ Sound off');
  soundButton.type='button';soundButton.setAttribute('aria-pressed','false');soundButton.title='Enable new-order bell and play a preview';
  document.getElementById('live-refresh')?.after(soundButton);
  let audioContext=null,soundOn=false,soundStarting=false,arrivalBaseline=null;
  const seenTickets=new Set();
  function soundState(){soundButton.textContent=soundOn?'♫ Sound on':'♫ Sound off';soundButton.setAttribute('aria-pressed',String(soundOn));soundButton.title=soundOn?'Mute new-order bell':'Enable new-order bell and play a preview';}
  function bell(){
    if(!soundOn||stopped||document.hidden)return;
    if(audioContext?.state!=='running'){soundOn=false;soundState();soundButton.title='Tap to enable sound again';return;}
    const start=audioContext.currentTime;
    for(const [offset,hz] of [[0,880],[.16,1174.66]]){
      for(const [multiple,volume] of [[1,.12],[2.76,.025]]){
        const oscillator=audioContext.createOscillator(),gain=audioContext.createGain(),at=start+offset;
        oscillator.type='sine';oscillator.frequency.value=hz*multiple;
        gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(volume,at+.008);gain.gain.exponentialRampToValueAtTime(.0001,at+.65);
        oscillator.connect(gain);gain.connect(audioContext.destination);oscillator.start(at);oscillator.stop(at+.7);
        oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
      }
    }
  }
  soundButton.addEventListener('click',async()=>{
    if(soundStarting||stopped)return;
    if(soundOn){soundOn=false;await audioContext?.suspend();soundState();return;}
    soundStarting=true;
    try{
      const Audio=window.AudioContext||window.webkitAudioContext;
      if(!Audio)throw new Error('Audio unavailable');
      audioContext=audioContext||new Audio();await audioContext.resume();
      if(stopped)return;
      soundOn=audioContext.state==='running';soundState();bell();
    }catch(error){soundOn=false;soundState();soundButton.title='Sound unavailable. Check browser and tablet audio settings.';}
    finally{soundStarting=false;}
  });
  function arrivals(data){
    const tickets=[...data.active,...data.recent];
    if(arrivalBaseline===null){arrivalBaseline=Math.max(0,...tickets.map(t=>t.id));tickets.forEach(t=>seenTickets.add(t.id));return;}
    const incoming=data.active.some(t=>t.id>arrivalBaseline&&!seenTickets.has(t.id));
    tickets.forEach(t=>seenTickets.add(t.id));
    if(incoming)bell(); // One chime per received batch, never one per item.
  }
  function customerCallout(ticket){
    if(ticket.kitchen_notes!==null&&ticket.kitchen_notes!==undefined)return {name:ticket.kitchen_customer_name||'',notes:ticket.kitchen_notes};
    const notes=(ticket.notes||'').trim();
    if(ticket.customer_name)return {name:ticket.customer_name,notes};
    const explicit=notes.match(/^name:\s*([^|\n]+)(?:[|\n]([\s\S]*))?$/i);
    if(explicit)return {name:explicit[1].trim(),notes:(explicit[2]||'').trim()};
    const words=notes.match(/^(\S+)(?:\s+([\s\S]*))?$/);
    return {name:words?words[1].replace(/[:,]$/,''):'',notes:words?(words[2]||'').trim():''};
  }
  function editNote(ticket){
    if(!available||stopped||pending.has(ticket.id))return;
    const callout=customerCallout(ticket),dialog=el('dialog','kitchen-note-editor'),form=el('form');
    form.append(el('strong',null,'Edit order '+ticket.number));
    const name=el('input'),notes=el('textarea');name.value=callout.name;name.maxLength=200;notes.value=callout.notes;notes.maxLength=4000;notes.rows=4;
    const nameLabel=el('label',null,'Customer name'),noteLabel=el('label',null,'Kitchen note');nameLabel.append(name);noteLabel.append(notes);form.append(nameLabel,noteLabel);
    const error=el('p','has-text-danger'),save=el('button','button is-primary','Save'),cancel=el('button','button','Cancel');save.type='submit';cancel.type='button';cancel.onclick=()=>dialog.close();form.append(error,save,cancel);
    form.onsubmit=event=>{event.preventDefault();if(!available||stopped||pending.has(ticket.id)){error.textContent='Reconnect or wait for this order to finish saving.';return;}act(ticket,'edit_note',null,{notes:notes.value.trim(),customer_name:name.value.trim()});dialog.close();};
    dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();name.focus();
  }
  function buttonsEnabled(){document.querySelectorAll('[data-kitchen-action]').forEach(b=>{const disabled=!available||(b.tagName==='BUTTON'&&pending.has(Number(b.dataset.ticketId)))||b.dataset.blocked==='true';if(b.tagName==='BUTTON')b.disabled=disabled;else b.setAttribute('aria-disabled',String(disabled));});}
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
    const callout=customerCallout(ticket);
    const root=el('article','store-order kitchen-ticket'+(ticket.completing?' ticket-completing':held?' ticket-held':finished?' ticket-ready':ticket.items.some(i=>i.state==='claimed')?' ticket-in-progress':'')),head=el('div','store-order-head');
    if(ticket.completing){root.setAttribute('aria-busy','true');root.setAttribute('aria-label','Order '+ticket.number+' completing; saving changes');}
    const title=el(finished?'strong':'button','kitchen-complete-header',(held?'Ⅱ Held · ':'')+callout.name);
    if(!finished){title.type='button';title.dataset.kitchenAction='done';title.dataset.ticketId=ticket.id;title.dataset.blocked=String(held);title.setAttribute('aria-label','Clear order '+ticket.number+' after pickup');title.title=held?'Resume using the timer before clearing':'Tap header after customer pickup to clear this order';title.addEventListener('click',event=>{event.stopPropagation();act(ticket,'done',null);});head.addEventListener('click',event=>{if(event.target===head)act(ticket,'done',null);});}
    const timer=el(finished?'strong':'button','kitchen-elapsed');timer.dataset.orderCreated=ticket.created_at;timer.dataset.held=String(held);timer.dataset.orderNumber=ticket.number;
    if(!finished){timer.type='button';timer.dataset.kitchenAction=held?'resume':'hold';timer.dataset.ticketId=ticket.id;timer.title=held?'Resume preparation':'Hold preparation; order age keeps counting';timer.addEventListener('click',event=>{event.stopPropagation();act(ticket,held?'resume':'hold',null);});}
    if(finished)timer.dataset.orderReady=ticket.ready_at;
    head.append(title,timer);root.append(head);
    if(!finished&&!ticket.completing&&ticket.items.length&&ticket.items.every(i=>i.state==='done'))root.append(el('strong','kitchen-pickup-label','✓ Ready for pickup'));
    const allergy=/\ballergy\b/i.test([ticket.notes,ticket.kitchen_notes,ticket.customer_name,ticket.kitchen_customer_name,...ticket.items.flatMap(i=>[i.name,...(i.modifiers||[]).flatMap(m=>[m.attribute,...m.values])])].filter(Boolean).join(' '));
    if(allergy){root.classList.add('ticket-allergy');root.append(el('strong','kitchen-allergy-label','⚠ ALLERGY'));}
    if(!finished){const edit=el('button','kitchen-edit-note','▤');edit.type='button';edit.title='Edit customer name or kitchen note';edit.setAttribute('aria-label','Edit customer name or note for order '+ticket.number);edit.dataset.kitchenAction='edit_note';edit.dataset.ticketId=ticket.id;edit.onclick=event=>{event.stopPropagation();editNote(ticket);};head.append(edit);}
    if(callout.notes)root.append(el('p','kitchen-callout','Order note: '+callout.notes));
    if(finished){const controls=el('div','kitchen-order-actions');controls.append(el('span','kitchen-state kitchen-done','✓ Picked up'),button('Undo order',ticket,'undo'));root.append(controls);}
    const table=el('table');table.setAttribute('aria-label','Order '+ticket.number+' preparation');
    const body=el('tbody');
    for(const item of ticket.items){
      const row=el('tr','kitchen-'+item.state),name=el('td','kitchen-item-name');
      name.append(el('strong','kitchen-quantity',String(item.quantity)),document.createTextNode('\u00a0\u00a0'+item.name));
      for(const group of item.modifiers||[])name.append(el('div','kitchen-modifier',group.attribute.replaceAll('_',' ')+': '+group.values.map(value=>value.replaceAll('_',' ')).join(', ')));
      if(item.saving){row.classList.add('kitchen-saving');const spinner=el('span','kitchen-save-indicator');spinner.setAttribute('role','img');spinner.setAttribute('aria-label','Saving changes');spinner.title='Saving changes';name.append(spinner);row.setAttribute('aria-busy','true');}
      if(!finished){
        const action=item.state==='available'?'claim':item.state==='claimed'?'done':'undo';
        const label={claim:'Claim',done:'Done',undo:'Undo'}[action];
        row.setAttribute('role','button');row.tabIndex=0;row.dataset.kitchenAction=action;row.dataset.ticketId=ticket.id;row.dataset.itemKey=item.key;
        row.setAttribute('aria-label',label+' item in order '+ticket.number);
        row.dataset.blocked=String(held);row.setAttribute('aria-disabled',String(!available||held));
        row.addEventListener('click',()=>act(ticket,action,item.key));
        row.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();act(ticket,action,item.key);}});
      }
      row.append(name);body.append(row);
    }
    table.append(body);root.append(table);return root;
  }
  function render(data,local=false){
    document.dispatchEvent(new CustomEvent('kitchen-flow',{detail:data.flow}));
    if(!local){arrivals(data);serverTime=Date.parse(data.generated_at);observed=performance.now();snapshot=data;}
    if(data.timer_profile){timerProfile=data.timer_profile;document.dispatchEvent(new CustomEvent('kitchen-timer-profile',{detail:timerProfile}));}
    const active=data.active.filter(t=>!pending.has(t.id));
    for(const queue of pending.values()){
      const ticket=structuredClone(queue.confirmed);
      for(const op of queue.ops){
        if(op.action==='edit_note'){ticket.kitchen_notes=op.extra.notes;ticket.kitchen_customer_name=op.extra.customer_name;continue;}
        if(op.action==='hold'||op.action==='resume'){ticket.state=op.action==='hold'?'held':'active';continue;}
        for(const item of ticket.items){if(op.key&&item.key!==op.key)continue;item.state=op.action==='claim'?(item.state==='available'?'claimed':item.state):op.action==='done'?'done':'available';item.saving=true;}
      }
      if(ticket.state==='ready')ticket.state='active';
      ticket.completing=queue.ops.some(op=>op.action==='done'&&!op.key);
      active.push(ticket);
    }
    active.sort((a,b)=>Date.parse(a.created_at)-Date.parse(b.created_at)||a.id-b.id);
    const oldest=active.find(t=>t.state!=='held'&&t.items.some(item=>item.state!=='done'));
    document.dispatchEvent(new CustomEvent('kitchen-wait',{detail:{created_at:oldest?.created_at||null,generated_at:new Date(serverTime+performance.now()-observed).toISOString()}}));
    const visibleRecent=data.recent.filter(t=>!pending.has(t.id));
    const next=JSON.stringify([active,visibleRecent]);
    if(next!==fingerprint){
      fingerprint=next;const container=feed.closest('.store-orders'),scroll=container.scrollTop,left=container.scrollLeft;
      const focus=document.activeElement,focusTicket=focus?.dataset.ticketId,focusKey=focus?.dataset.itemKey;
      feed.replaceChildren(...active.map(t=>card(t)));recent.replaceChildren(...visibleRecent.map(t=>card(t,true)));
      if(!active.length)feed.append(el('p','live-empty','No unfinished orders.'));
      document.getElementById('kitchen-count').textContent=active.length+' active';
      document.getElementById('kitchen-recent-count').textContent='('+visibleRecent.length+')';
      container.scrollTop=scroll;
      container.scrollLeft=left;
      if(focusTicket&&focusKey)feed.querySelector('[data-ticket-id="'+focusTicket+'"][data-item-key="'+focusKey+'"]')?.focus({preventScroll:true});
    }
    tick();buttonsEnabled();
  }
  function revoke(){
    stopped=true;available=false;source?.close();source=null;controller?.abort();
    soundOn=false;soundState();soundButton.disabled=true;audioContext?.close().catch(()=>{});
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
  async function act(ticket,action,key,extra={}){
    if(!available||stopped)return;
    if(pending.has(ticket.id)&&!key)return;
    if(ticket.state==='held'&&action!=='resume'&&action!=='edit_note')return;
    const existing=pending.get(ticket.id),queue=existing||{confirmed:structuredClone(ticket),ops:[]};
    const item=key?ticket.items.find(i=>i.key===key):null;
    queue.ops.push({action,key,extra,from:item?.state,identity:key?itemIdentity(item):null});
    pending.set(ticket.id,queue);if(snapshot)render(snapshot,true);
    if(!existing)saveQueue(ticket.id,queue);
  }
  function remember(ticket){
    if(!snapshot)return;
    const current=[...snapshot.active,...snapshot.recent].find(t=>t.id===ticket.id);
    if(current&&current.revision>ticket.revision)return;
    snapshot={...snapshot,active:snapshot.active.filter(t=>t.id!==ticket.id),recent:snapshot.recent.filter(t=>t.id!==ticket.id)};
    if(ticket.state==='active'||ticket.state==='held')snapshot.active.push(ticket);
    else if(ticket.state==='ready'){snapshot.recent.unshift(ticket);snapshot.recent=snapshot.recent.slice(0,20);}
  }
  async function saveQueue(id,queue){
    let message='';
    try{
      while(queue.ops.length&&!stopped){
      const op=queue.ops[0],ticket=queue.confirmed;
      const item=op.key?ticket.items.find(i=>i.key===op.key):null;
      if(op.key&&(item?.state!==op.from||itemIdentity(item)!==op.identity))throw new Error('Item changed while saving. Review the current order before continuing.');
      const response=await fetch(base+'/'+id,{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Kitchen-CSRF':feed.dataset.csrf},
        body:JSON.stringify({action:op.action,revision:ticket.revision,item_key:op.key,...op.extra}),signal:AbortSignal.timeout(10000)});
      if(response.status===401||response.status===403){revoke();document.dispatchEvent(new Event('store-access-denied'));return;}
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Action could not be saved.');
      let saved=data.ticket;
      if(!saved){await refresh(true);saved=[...(snapshot?.active||[]),...(snapshot?.recent||[])].find(t=>t.id===id);}
      if(!saved)throw new Error('Saved order could not be reviewed.');
      queue.confirmed=saved;queue.ops.shift();remember(saved);if(snapshot&&!stopped)render(snapshot,true);
      }
    }catch(error){message=error.name==='TimeoutError'?'Save response timed out. Reviewing the saved queue before another tap.':error.message;}
    finally{pending.delete(id);if(message)await refresh(true);if(snapshot&&!stopped)render(snapshot,true);buttonsEnabled();document.dispatchEvent(new Event('kitchen-queue-changed'));if(message&&!stopped)status.textContent='Order '+queue.confirmed.number+': '+message+' Remaining queued taps for this order were cancelled.';}
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
  window.addEventListener('pagehide',()=>{stopped=true;soundOn=false;audioContext?.close().catch(()=>{});source?.close();controller?.abort();feed.replaceChildren();recent.replaceChildren();});
  setInterval(()=>{if(!document.hidden)tick();},1000);
  setInterval(refresh,5000);refresh();connect();
})();
