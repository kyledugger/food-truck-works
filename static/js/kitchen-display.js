(() => {
  'use strict';
  const feed=document.getElementById('store-order-feed'),grid=document.getElementById('live-stores');
  const storeId=feed?.dataset.storeId||grid?.dataset.storeId,board=!!document.querySelector('[data-kitchen-board]');
  if(feed?.dataset.kitchen!=='true'||!storeId)return;
  const readonly=feed.dataset.readonly==='true';
  const base='/dashboard/stores/'+storeId+(readonly?'/kitchen-watch':feed.dataset.test==='true'?'/kitchen-test':'/kitchen');
  const recent=document.getElementById('kitchen-recent-feed'),status=document.getElementById('kitchen-status');
  let source=null,loading=false,stopped=false,available=false,live=false,fingerprint='',controller=null,refreshTask=null;
  const pending=new Map();
  let timerProfile={green_seconds:0,yellow_seconds:300,red_seconds:600};
  let markers={rules:[],revision:0};
  const normalizeMarkerText=text=>String(text).replaceAll('_',' ').toLowerCase();
  function marked(text,target){
    const matching=markers.rules.filter(rule=>rule.target===target&&normalizeMarkerText(text).includes(normalizeMarkerText(rule.text)));
    const prefix=[...new Set(matching.map(rule=>rule.marker))].join(' ');
    return (prefix?prefix+' ':'')+text;
  }
  let serverTime=Date.now(),observed=performance.now(),snapshot=null;
  const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const itemIdentity=item=>JSON.stringify([item?.key,item?.name,item?.quantity,item?.modifiers||[]]);
  const soundButton=el('button',board?'button':'button is-small is-light','♫ Sound off');
  soundButton.type='button';soundButton.setAttribute('aria-pressed','false');soundButton.title='Enable new-order bell and play a preview';
  document.getElementById('live-refresh')?.after(soundButton);
  let audioContext=null,soundOn=false,soundStarting=false,arrivalBaseline=null;
  const seenTickets=new Set();
  const serviceControls=el('div','buttons are-small');
  const startService=el('button','button is-success','Start service');
  const resumeService=el('button','button is-light','Resume previous queue');
  resumeService.title='Continue the previous service with its uncleared tickets. Start service begins fresh.';
  const endService=el('button','button is-light','End service');
  const clearService=el('button','button is-light','Clear queue');
  const recoverService=el('button','button is-light','Get recent past orders');
  const recoveryMenu=el('details','kitchen-ticket-menu'),ticketSummary=el('summary','');
  const ticketArrow=el('span','','▸');ticketArrow.setAttribute('aria-hidden','true');ticketArrow.style.marginLeft='auto';
  ticketSummary.append(el('strong','','Ticket options'),ticketArrow);ticketSummary.setAttribute('aria-expanded','false');recoveryMenu.append(ticketSummary);
  recoveryMenu.addEventListener('toggle',()=>{ticketArrow.textContent=recoveryMenu.open?'▾':'▸';ticketSummary.setAttribute('aria-expanded',String(recoveryMenu.open));});
  const recoveryLater=el('button','button is-small','Get recent past orders');recoveryMenu.append(recoveryLater);
  const archiveReview=el('button','button is-small','Review cleared tickets');
  recoveryMenu.replaceChildren(recoveryMenu.firstChild,resumeService,clearService,recoveryLater,archiveReview);
  serviceControls.append(startService,recoverService);
  for(const button of [startService,resumeService,recoverService,recoveryLater,clearService,endService]){button.hidden=true;button.style.display='none';}
  let recoveryShortcutUntil=0;
  serviceControls.style.cssText='display:flex;gap:.25rem;align-items:center;flex-wrap:wrap;margin:0';
  if(feed.dataset.test!=='true'){
    const toolbar=document.querySelector('.board-toolbar nav')||document.querySelector('.kitchen-heading-row');
    if(toolbar){toolbar.style.flexWrap='wrap';toolbar.prepend(serviceControls);}else status.before(serviceControls);
  }
  const refreshButton=document.getElementById('live-refresh');
  if(refreshButton){refreshButton.textContent='↻';refreshButton.setAttribute('aria-label','Refresh');refreshButton.title='Check Poynt for current orders';refreshButton.style.fontSize='1.35rem';}
  const moreMenu=el('details','kitchen-more-menu'),moreToggle=el('summary','button','⋯');
  moreToggle.setAttribute('aria-label','More kitchen options');moreToggle.title='More kitchen options';
  const morePanel=el('div','kitchen-more-panel');moreMenu.append(moreToggle,morePanel);
  morePanel.append(soundButton);
  if(feed.dataset.canMarkers==='true'&&!readonly){
    const markerButton=el('button','button','Kitchen item markers');morePanel.append(markerButton);
    markerButton.onclick=()=>{
      const revision=markers.revision,dialog=el('dialog','kitchen-marker-editor'),form=el('form'),rows=el('div'),message=el('p');
      form.append(el('h2','','Kitchen item markers'),el('p','','Shared across all stores in this organization. Rules match anywhere, ignoring case. Modifier underscores match spaces.'),rows);
      function addRule(rule={target:'item',text:'',marker:''}){
        const row=el('div','marker-rule'),target=el('select'),text=el('input'),emoji=el('input'),remove=el('button','button','Remove');
        for(const [value,label] of [['item','Item name'],['modifier','Modifier value']]){const option=el('option','',label);option.value=value;target.append(option);}target.value=rule.target;target.setAttribute('aria-label','Rule target');
        text.value=rule.text;text.placeholder='Name contains (e.g. banana)';text.maxLength=100;text.required=true;text.setAttribute('aria-label','Matching text');
        emoji.value=rule.marker;emoji.placeholder='Paste emoji';emoji.maxLength=32;emoji.required=true;emoji.setAttribute('aria-label','Emoji marker');remove.type='button';remove.onclick=()=>row.remove();row.append(target,text,emoji,remove);rows.append(row);
      }
      markers.rules.forEach(addRule);
      const add=el('button','button','Add rule'),save=el('button','button','Save'),cancel=el('button','button','Cancel');add.type=cancel.type='button';save.type='submit';add.onclick=()=>{if(rows.children.length<50)addRule();};cancel.onclick=()=>dialog.close();form.append(add,message,save,cancel);
      form.onsubmit=async event=>{
        event.preventDefault();save.disabled=true;message.textContent='Saving…';
        const rules=[...rows.children].map(row=>({target:row.querySelector('select').value,text:row.querySelectorAll('input')[0].value.trim(),marker:row.querySelectorAll('input')[1].value.trim()}));
        try{const response=await fetch('/dashboard/stores/'+storeId+'/kitchen-markers',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Kitchen-CSRF':feed.dataset.csrf},body:JSON.stringify({rules,revision})});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Check the rules and try again.');markers=data;dialog.close();await refresh(true);}catch(error){message.textContent=error.message;}finally{save.disabled=false;}
      };
      dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
    };
  }
  const metricsPanel=document.getElementById('kitchen-metrics'),metricsToggle=el('button','button','Kitchen metrics');
  let metricsOn=false;try{metricsOn=localStorage.getItem('kitchen-metrics:'+storeId)==='true';}catch{}
  const showMetrics=()=>{if(metricsPanel)metricsPanel.hidden=!metricsOn;metricsToggle.setAttribute('aria-pressed',String(metricsOn));};
  metricsToggle.onclick=()=>{metricsOn=!metricsOn;try{localStorage.setItem('kitchen-metrics:'+storeId,String(metricsOn));}catch{}showMetrics();};showMetrics();morePanel.append(metricsToggle);
  document.addEventListener('kitchen-flow',event=>{
    if(!metricsPanel)return;const f=event.detail;metricsPanel.replaceChildren();
    if(!f?.tracking){metricsPanel.append(el('p','','Start service to see kitchen metrics.'));return;}
    for(const [label,value] of [['Kitchen Intake',f.intake?.items_per_minute],['Kitchen Completion',f.completion?.items_per_minute]]){const cell=el('div');cell.append(el('small','',label),el('strong','',value==null?'—':value.toFixed(1)+' items/min'),el('small','','Last 15 minutes'));metricsPanel.append(cell);}
    const wait=el('div');wait.append(el('small','','Customer Wait'),el('strong','',f.customer_wait?Math.floor(f.customer_wait.wait_seconds/60)+'m '+f.customer_wait.wait_seconds%60+'s':'No waiting tickets'));metricsPanel.append(wait);
    const pace=el('div'),table=el('table'),head=el('tr'),values=el('tr');pace.append(el('small','','Time between orders'));head.append(el('th','','# Items'));values.append(el('th','','Seconds'));
    for(const size of [1,2,3]){head.append(el('th','',size));const value=f.order_pace?.find(p=>p.items===size)?.recent_seconds;values.append(el('td','',value==null?'—':value.toFixed(1)));}table.append(head,values);pace.append(table);metricsPanel.append(pace);
  });
  document.addEventListener('kitchen-flow-unavailable',()=>{if(metricsPanel)metricsPanel.replaceChildren(el('p','','Kitchen metrics temporarily unavailable.'));});
  if(feed.dataset.test!=='true')morePanel.append(recoveryMenu);
  const timerButton=document.getElementById('timer-settings');if(timerButton)morePanel.append(timerButton);
  const toolbar=document.querySelector('.board-toolbar nav')||document.querySelector('.kitchen-heading-row');
  const testLink=toolbar&&[...toolbar.querySelectorAll('a')].find(a=>/^(Test kitchen|Live kitchen|Test queue|Live queue)$/.test(a.textContent.trim()));
  if(testLink)morePanel.append(testLink);
  if(readonly){serviceControls.remove();recoveryMenu.remove();endService.remove();testLink?.remove();}
  if(feed.dataset.test!=='true')morePanel.append(endService);
  if(toolbar)toolbar.append(moreMenu);else refreshButton?.after(moreMenu);
  const menuStyle=el('style','');menuStyle.textContent=`
    .kitchen-more-menu{position:relative;align-self:center;flex-shrink:0}
    .kitchen-more-menu>summary{list-style:none;min-width:40px;cursor:pointer}
    .kitchen-more-menu>summary::-webkit-details-marker{display:none}
    .kitchen-more-panel{position:absolute;right:0;top:calc(100% + 6px);z-index:1000;width:260px;max-width:calc(100vw - 24px);max-height:75vh;box-sizing:border-box;overflow-y:auto;overflow-x:hidden;background:white;border:1px solid #c8cdd2;border-radius:8px;padding:6px;box-shadow:0 6px 20px #0003}
    .kitchen-more-panel *{box-sizing:border-box;min-width:0}
    .kitchen-more-panel .button,.kitchen-more-panel summary{display:flex;width:100%;justify-content:flex-start;text-align:left;height:auto!important;min-height:34px!important;margin:0!important;border:0;border-radius:4px;font-size:.85rem;line-height:1.25;padding:6px 10px!important;white-space:normal;overflow-wrap:anywhere;color:#30363b;background:white;cursor:pointer;box-shadow:none!important}
    .kitchen-more-panel .button:hover,.kitchen-more-panel summary:hover{background:#edf0f3}
    .kitchen-ticket-menu>summary{background:#f0f2f4;list-style:none;gap:12px}
    .kitchen-ticket-menu>summary::-webkit-details-marker{display:none}
    .kitchen-ticket-menu>summary>span{font-size:1.15rem;flex-shrink:0}
    .kitchen-ticket-menu .button{padding-left:22px!important}
    .board-toolbar nav{align-items:center}
    .board-toolbar nav>.button,.board-toolbar .kitchen-more-menu>summary,.board-toolbar .buttons>.button{box-sizing:border-box;height:36px!important;min-height:36px!important;padding:4px 12px;font-size:.85rem;line-height:1.2;margin:0}
    .kitchen-more-panel [hidden]{display:none!important}
  `;document.head.append(menuStyle);
  document.addEventListener('click',event=>{if(!moreMenu.contains(event.target))moreMenu.open=false;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&moreMenu.open){moreMenu.open=false;moreToggle.focus();}});
  morePanel.addEventListener('click',event=>{if(event.target.closest('button,a'))moreMenu.open=false;});
  let serviceBusy=false;
  function serviceState(){
    if(readonly||!snapshot?.session||feed.dataset.test==='true')return;
    const s=snapshot.session,on=s.active&&!s.needs_resume;
    startService.hidden=on;startService.textContent='Start service';
    resumeService.hidden=!s.needs_resume;endService.hidden=!s.active;clearService.hidden=!on;
    recoverService.hidden=!on||performance.now()>=recoveryShortcutUntil;recoveryLater.hidden=!on;
    for(const node of [startService,resumeService,recoverService,recoveryLater,clearService,endService])node.style.display=node.hidden?'none':'';
    for(const b of [startService,resumeService,recoverService,recoveryLater,clearService,endService,archiveReview])b.disabled=serviceBusy||pending.size>0;
  }
  async function serviceRequest(action,extra={}){
    const response=await fetch(base+'/service',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-Kitchen-CSRF':feed.dataset.csrf},
      body:JSON.stringify({action,revision:snapshot?.session?.revision||0,...extra})});
    const result=await response.json();if(!response.ok)throw new Error(result.detail||'Kitchen service could not change.');return result;
  }
  async function changeService(action){
    if(serviceBusy)return;serviceBusy=true;serviceState();
    try{await serviceRequest(action);if(action==='start')recoveryShortcutUntil=performance.now()+60000;await refresh(true);}
    catch(error){await refresh(true);status.textContent=error.message;}
    finally{serviceBusy=false;serviceState();}
  }
  startService.addEventListener('click',()=>changeService('start'));
  resumeService.addEventListener('click',()=>changeService('resume'));
  endService.addEventListener('click',()=>{if(confirm('End service and archive remaining tickets without counting completions?'))changeService('end');});
  clearService.addEventListener('click',()=>{if(confirm('Clear open tickets without counting them as completed?'))changeService('clear');});
  async function recoverOrders(){
    if(serviceBusy)return;serviceBusy=true;serviceState();
    const dialog=el('dialog','');dialog.style.maxWidth='min(600px,90vw)';dialog.style.maxHeight='85vh';dialog.style.overflowY='auto';
    const title=el('h3','','Get recent past orders'),windowSelect=el('select','');
    for(const minutes of [10,30,60]){const option=el('option','','Last '+minutes+' minutes before service started');option.value=minutes;windowSelect.append(option);}
    const message=el('p',''),list=el('div',''),search=el('button','button','Search'),include=el('button','button is-success','Include selected orders'),skip=el('button','button','Skip');
    dialog.append(title,windowSelect,search,message,list,include,skip);document.body.append(dialog);dialog.showModal();
    let ids=[];
    async function searchOrders(){
      search.disabled=true;include.disabled=true;message.textContent='Checking Poynt…';list.replaceChildren();
      try{
        // A successful provider check is required; cache emptiness is not proof of no recent orders.
        const response=await fetch(base+'/refresh',{method:'POST',credentials:'same-origin',signal:AbortSignal.timeout(65000),headers:{'X-Kitchen-CSRF':feed.dataset.csrf}});
        const current=await response.json();if(!response.ok)throw new Error(current.detail||'Could not check Poynt. Retry.');render(current);
        const result=await serviceRequest('recent',{minutes:Number(windowSelect.value)});ids=result.orders.map(o=>o.id);
        message.textContent=ids.length?result.orders.length+' eligible orders found. Select orders to include.':'No eligible recent orders found.';
        for(const order of result.orders){const label=el('label','');label.style.display='block';const check=el('input','');check.type='checkbox';check.checked=true;check.value=order.id;label.append(check,document.createTextNode(' '+order.number+' · '+order.items.join(', ')));list.append(label);}
        include.disabled=!ids.length;
      }catch(error){ids=[];message.textContent=error.message;}
      finally{search.disabled=false;}
    }
    search.addEventListener('click',searchOrders);windowSelect.addEventListener('change',searchOrders);
    include.addEventListener('click',async()=>{include.disabled=true;try{await serviceRequest('import',{minutes:Number(windowSelect.value),order_ids:[...list.querySelectorAll('input:checked')].map(i=>i.value)});dialog.close();await refresh(true);}catch(error){message.textContent=error.message;include.disabled=false;}});
    skip.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{dialog.remove();serviceBusy=false;serviceState();});await searchOrders();
  }
  recoverService.addEventListener('click',recoverOrders);recoveryLater.addEventListener('click',recoverOrders);
  archiveReview.addEventListener('click',async()=>{
    if(serviceBusy)return;serviceBusy=true;serviceState();
    try{
      const result=await serviceRequest('archived'),dialog=el('dialog','');dialog.style.cssText='max-width:min(600px,90vw);max-height:85vh;overflow:auto';
      dialog.append(el('h3','','Cleared without tracking · Latest 100'));
      if(!result.tickets.length)dialog.append(el('p','','No cleared tickets.'));
      for(const ticket of result.tickets){const callout=customerCallout(ticket),section=el('section','');section.append(el('strong','',ticket.number+' · '+callout.name));for(const item of ticket.items)section.append(el('p','',item.quantity+'  '+item.name+(item.modifiers?.length?' · '+item.modifiers.join(', '):'')));if(callout.notes)section.append(el('p','',callout.notes));dialog.append(section);}
      const close=el('button','button','Close');close.onclick=()=>dialog.close();dialog.append(close);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
    }catch(error){status.textContent=error.message;}finally{serviceBusy=false;serviceState();}
  });
  setInterval(serviceState,1000);
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
    return {
      name:ticket.kitchen_customer_name??ticket.customer_name??'',
      notes:ticket.kitchen_notes??ticket.notes??''
    };
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
    const title=el(finished?'strong':'button','kitchen-complete-header',(held?'Ⅱ Held · ':'')+(callout.name.trim()||'#'+ticket.number));
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
      name.append(el('strong','kitchen-quantity',String(item.quantity)),document.createTextNode('\u00a0\u00a0'+marked(item.name,'item')));
      for(const group of item.modifiers||[])name.append(el('div','kitchen-modifier',group.attribute.replaceAll('_',' ')+': '+group.values.map(value=>marked(value.replaceAll('_',' '),'modifier')).join(', ')));
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
    table.append(body);root.append(table);
    if(readonly){root.querySelectorAll('[data-kitchen-action]').forEach(node=>{if(node.classList.contains('kitchen-edit-note')||node.classList.contains('kitchen-action'))node.remove();else{node.removeAttribute('data-kitchen-action');node.removeAttribute('role');node.removeAttribute('tabindex');node.removeAttribute('title');if(node.tagName==='BUTTON')node.disabled=true;}});}
    return root;
  }
  function render(data,local=false){
    if(data.markers)markers=data.markers;
    document.dispatchEvent(new CustomEvent('kitchen-flow',{detail:data.flow}));
    if(!local){arrivals(data);serverTime=Date.parse(data.generated_at);observed=performance.now();snapshot=data;serviceState();}
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
    const next=JSON.stringify([active,visibleRecent,markers]);
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
  async function refresh(force=false,provider=false){
    if(stopped||document.hidden)return;
    if(loading){await refreshTask;if(force===true||provider)return refresh(force,provider);return;}
    refreshTask=load(provider);await refreshTask;
  }
  async function load(provider=false){
    if(readonly)provider=false;
    loading=true;controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),provider?65000:10000);
    const button=document.getElementById('live-refresh');
    if(provider){if(button)button.disabled=true;status.textContent='Checking Poynt for current orders…';}
    try{
      const response=await fetch(provider?base+'/refresh':base,{method:provider?'POST':'GET',
        headers:provider?{'X-Kitchen-CSRF':feed.dataset.csrf}:{},credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(response.status===401||response.status===403){revoke();document.dispatchEvent(new Event('store-access-denied'));return;}
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Queue could not refresh.');
      available=true;render(data);status.textContent=live?(feed.dataset.test==='true'?'Shared test kitchen updates':'Live kitchen updates'):'Kitchen updates · polling fallback every 5 seconds';
      if(data.session&&!data.session.active)status.textContent='Kitchen tracking off. Start service to receive new tickets.';
      else if(data.session?.needs_resume)status.textContent='Previous service found. Resume it or start fresh.';
      if(provider)document.dispatchEvent(new Event('kitchen-queue-changed'));
    }catch(error){if(!stopped){available=false;document.dispatchEvent(new Event('kitchen-flow-unavailable'));buttonsEnabled();status.textContent=(error.name==='AbortError'?'Queue refresh timed out.':error.message)+' Actions paused until the queue reconnects.';}}
    finally{clearTimeout(timeout);controller=null;loading=false;if(provider&&button)button.disabled=stopped;}
  }
  async function act(ticket,action,key,extra={}){
    if(readonly)return;
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
  document.getElementById('live-refresh')?.addEventListener('click',()=>refresh(true,feed.dataset.test!=='true'));
  document.addEventListener('store-data',refresh);
  window.addEventListener('pagehide',()=>{stopped=true;soundOn=false;audioContext?.close().catch(()=>{});source?.close();controller?.abort();feed.replaceChildren();recent.replaceChildren();});
  setInterval(()=>{if(!document.hidden)tick();},1000);
  setInterval(refresh,5000);refresh();connect();
})();
