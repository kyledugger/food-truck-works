(() => {
  'use strict';
  const grid = document.getElementById('live-stores');
  if (!grid) return;
  const status = document.getElementById('live-status');
  const refreshed = document.getElementById('live-refreshed');
  const enable = document.getElementById('enable-live-webhook');
  const dateInput=document.getElementById('live-date'),todayButton=document.getElementById('live-today'),previousButton=document.getElementById('live-previous'),nextButton=document.getElementById('live-next');
  let selection=new URL(location.href).searchParams.get('date')||'today', lastData=null,controller=null;
  const storeView=grid.dataset.storeId;
  if(storeView)selection='today';
  const cards = new Map();
  let timer, inFlight = false;
  const money = cents => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(cents/100);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n; };
  function ago(at, now) {
    if (!at) return 'No sale recorded in the current reporting window';
    const seconds = Math.max(0, Math.floor((Date.parse(now)-Date.parse(at))/1000));
    if(seconds<60)return 'Last sale '+seconds+' seconds ago';
    if(seconds<3600)return 'Last sale '+Math.floor(seconds/60)+' minutes ago';
    return 'Last sale '+Math.floor(seconds/3600)+' hours ago';
  }
  function makeCard(id) {
    const root=el('article','live-store');
    const header=el('div','live-store-header'), info=el('div');
    const name=el('h2'), date=el('p','live-store-date'), badge=el('span','tag is-light');
    const storeLink=el('a','live-store-link');storeLink.href='/dashboard/stores/'+id;storeLink.append(name);info.append(storeLink,date);header.append(info,badge);root.append(header);
    const message=el('p','live-empty');root.append(message);
    const body=el('div'),kpis=el('div','live-kpis');
    const fields={},labels={};
    for (const [key,label] of [['sales','Sales'],['tips','Tips today'],['orders','Orders today'],['items','Items today']]) {
      const cell=el('div');fields[key]=el('strong');labels[key]=el('small','',label);cell.append(labels[key],fields[key]);kpis.append(cell);
    }
    body.append(kpis);
    const now=el('div','live-now'), recent=el('strong'),pace=el('p','live-pace'),last=el('p','live-last');
    now.append(el('small','','LAST 5 MINUTES'),recent,pace,last);body.append(now);
    const warning=el('p','live-warning');body.append(warning);
    const details=el('details'),summary=el('summary','','Hourly sales & category totals');details.append(summary);
    const avg=el('p','is-size-7 mt-2');details.append(avg);
    const hourlyWrap=el('div','live-chart'),hourly=el('canvas');hourly.setAttribute('role','img');hourlyWrap.append(hourly);
    details.append(el('p','live-chart-title','Hourly Sales'),hourlyWrap);
    const activityWrap=el('div','live-chart live-chart-small'),activity=el('canvas');activity.setAttribute('role','img');activityWrap.append(activity);
    const activityTitle=el('p','live-chart-title','Orders per 5 minutes · past hour');
    if(!storeView)details.append(activityTitle,activityWrap);
    if(storeView){const products=el('button','button is-small is-light','Category totals · View product SKU counts');products.type='button';products.addEventListener('click',()=>document.dispatchEvent(new CustomEvent('store-products')));details.append(products);}
    const table=el('table','live-category'),head=el('thead'),tr=el('tr');
    ['Category','Qty','Sales','Share'].forEach(label=>tr.append(el('th','',label)));head.append(tr);
    const rows=el('tbody');table.append(head,rows);details.append(table);body.append(details);root.append(body);grid.append(root);
    const card={root,name,date,badge,message,body,fields,labels,now,recent,pace,last,warning,details,avg,rows,hourly,activity,activityWrap,activityTitle,charts:{}};
    details.addEventListener('toggle',()=>{if(details.open && card.data)drawCharts(card,card.data);});
    cards.set(id,card);return card;
  }
  function chart(card,key,canvas,points,isMoney) {
    if(!window.Chart){canvas.parentElement.textContent='Chart unavailable. Refresh when the chart library is accessible.';return;}
    const labels=points.map(p=>p.label+(p.partial?' · partial':'')),values=points.map(p=>isMoney?p.sales_cents/100:p.orders);
    canvas.setAttribute('aria-label',points.map((p,i)=>labels[i]+': '+(isMoney?money(p.sales_cents):p.orders+' orders')).join('; '));
    if(card.charts[key]){card.charts[key].data.labels=labels;card.charts[key].data.datasets[0].data=values;card.charts[key].update('none');return;}
    card.charts[key]=new Chart(canvas,{type:'bar',data:{labels,datasets:[{label:isMoney?'Sales':'Orders',data:values,
      backgroundColor:'#16835f',borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>isMoney?money(Math.round(ctx.parsed.y*100)):ctx.parsed.y+' orders'}}},
      scales:{y:{beginAtZero:true,grid:{color:'#dbe4e8',lineWidth:1},ticks:{precision:0,callback:value=>isMoney?'$'+value:value}},x:{ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:8}}}}});
  }
  function drawCharts(card,s){chart(card,'hourly',card.hourly,s.hourly,true);if(!storeView&&!s.historical)chart(card,'activity',card.activity,s.activity,false);}
  function render(data) {
    lastData=data;
    const mode=document.getElementById('live-mode');
    const delayed=data.error||data.stale||data.queue_delayed;
    if(mode){mode.textContent=data.historical_view?'Historical view · not live':delayed?'Updates delayed':data.initializing?'Loading sales':'Live · refresh every 15 seconds';mode.className='tag '+(data.historical_view?'is-info is-light':'is-success is-light');mode.classList.toggle('live-mode-active',!data.historical_view&&!delayed&&!data.initializing&&data.connected&&data.stores.some(s=>!s.setup_required));mode.title=data.webhook_registered?'Order webhooks enabled':'Periodic order sync';}
    if(dateInput){dateInput.min=data.earliest_date||'';dateInput.max=data.latest_date||'';dateInput.value=/^\d{4}-\d{2}-\d{2}$/.test(selection)?selection:selection==='today'?data.latest_date||'':data.stores.find(s=>s.date)?.date||'';}
    updateArrows();
    if(todayButton)todayButton.className='button is-small '+(selection==='today'?'live-today-active':'is-light');
    const alive=new Set();
    for(const s of data.stores){
      alive.add(s.id);const c=cards.get(s.id)||makeCard(s.id);c.name.textContent=s.name;
      c.message.hidden=true;c.body.hidden=false;c.badge.textContent='';
      if(s.setup_required){c.date.textContent='Setup needed';c.body.hidden=true;c.message.hidden=false;c.message.textContent=s.message;continue;}
      c.data=s;c.date.textContent=s.date+' · '+s.timezone;
      const when=s.historical?'on this date':'today';
      c.badge.textContent=(s.loading??data.initializing)?'Loading sales':s.historical?'Historical view':s.order_count?'Trading today':s.currency_warning?'No USD sales today':'No sales today';
      c.labels.sales.textContent='Sales';c.labels.tips.textContent=s.historical?'Tips':'Tips today';c.labels.orders.textContent=s.historical?'Orders':'Orders today';c.labels.items.textContent=s.historical?'Items':'Items today';
      c.now.hidden=!!s.historical;c.activityWrap.hidden=!!s.historical;c.activityTitle.hidden=!!s.historical;
      if(s.historical&&c.charts.activity){c.charts.activity.destroy();delete c.charts.activity;}
      if(s.loading??data.initializing){c.body.hidden=true;c.message.hidden=false;c.message.textContent='Collecting orders for '+s.date+'…';continue;}
      if(!c.initialized){c.details.open=!!s.order_count||!!storeView;c.initialized=true;}
      if(!s.order_count){c.message.hidden=false;c.message.textContent=s.currency_warning?'No USD sales '+when+'. Other currencies are excluded.':'No sales '+when+'.';c.body.hidden=!storeView&&!s.recent_orders&&!s.currency_warning;}
      c.fields.sales.textContent=money(s.sales_cents);c.fields.tips.textContent=money(s.tips_cents);c.fields.orders.textContent=s.order_count;c.fields.items.textContent=new Intl.NumberFormat('en-US').format(s.item_count||0);
      if(!s.historical){c.recent.textContent=s.recent_orders+' orders · '+money(s.recent_sales_cents);
        c.pace.textContent=s.pace+(s.pace_ratio!==null?' · '+s.pace_ratio.toFixed(1)+'× recent average':'');c.last.textContent=ago(s.last_sale_at,data.generated_at);}
      c.avg.textContent='Average sale: '+money(s.average_sale_cents)+(s.historical?' · Full day.':' · Current hour is partial.');
      c.warning.hidden=!s.currency_warning;c.warning.textContent='Non-USD orders are excluded from these totals.';
      c.rows.replaceChildren();
      for(const row of s.categories){const tr=el('tr');[row.name,row.quantity,money(row.sales_cents),row.share+'%'].forEach(v=>tr.append(el('td','',v)));c.rows.append(tr);}
      if(!s.categories.length){const tr=el('tr'),td=el('td','','No items sold '+when);td.colSpan=4;tr.append(td);c.rows.append(tr);}
      if(c.details.open)drawCharts(c,s);
    }
    for(const [id,c]of cards){if(!alive.has(id)){Object.values(c.charts).forEach(chart=>chart.destroy());c.root.remove();cards.delete(id);}}
    let text;
    if(!data.connected)text='Connect Poynt to see store sales.';
    else if(!data.stores.length)text='Add or activate stores in Store Settings to start tracking today.';
    else if(data.error)text=data.error+' Displayed totals may be out of date.';
    else if(data.queue_delayed)text='Order updates are delayed. Displayed totals may be out of date.';
    else if(data.initializing)text='Collecting the selected day’s orders. This can take a moment on the first visit.';
    else if(data.stale)text='Waiting for an order sync. Displayed totals may be out of date.';
    else text='';
    status.hidden=!text;status.textContent=text;status.className='notification '+(delayed?'is-warning is-light':'is-light');
    const loaded=data.historical_view?data.last_view_loaded_at:data.generated_at;
    refreshed.textContent=(data.historical_view?'Sales loaded ':'Updated ')+(loaded?new Date(loaded).toLocaleTimeString():'—');
    if(enable)enable.hidden=!!data.historical_view||!data.can_enable_webhook||data.webhook_registered;
    if(data.polling_needed===false)clearInterval(timer);
    if(storeView)document.dispatchEvent(new CustomEvent('store-data',{detail:data}));
  }
  async function refresh() {
    if(inFlight||document.hidden)return;
    inFlight=true;
    const requestedSelection=selection;
    controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch('/dashboard/data?'+new URLSearchParams(storeView?{date:'today',store_id:storeView}:{date:requestedSelection}),{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(response.status===401){location.assign('/login');return;}
      if(response.status===403){
        clearInterval(timer);for(const c of cards.values()){Object.values(c.charts).forEach(chart=>chart.destroy());c.root.remove();}cards.clear();
        if(enable)enable.hidden=true;
        document.getElementById('live-mode').textContent='Access denied';refreshed.textContent='';
        document.dispatchEvent(new Event('store-access-denied'));
      }
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Dashboard could not refresh.');if(requestedSelection===selection)render(data);
    }catch(error){if(error.name!=='AbortError' || requestedSelection===selection){status.hidden=false;document.getElementById('live-mode')?.classList.remove('live-mode-active');status.textContent=error.message+' Displayed totals may be out of date.';status.className='notification is-warning is-light';}}
    finally{clearTimeout(timeout);controller=null;inFlight=false;if(requestedSelection!==selection)refresh();}
  }
  if(enable)enable.addEventListener('click',async()=>{
    enable.disabled=true;enable.classList.add('is-loading');
    try{const response=await fetch('/dashboard/webhook/enable',{method:'POST',credentials:'same-origin',headers:{'X-Requested-With':'FoodTruckWorks'},signal:AbortSignal.timeout(45000)});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not enable webhooks.');await refresh();
    }catch(error){status.hidden=false;document.getElementById('live-mode')?.classList.remove('live-mode-active');status.textContent=error.message;status.className='notification is-warning is-light';}
    finally{enable.disabled=false;enable.classList.remove('is-loading');}
  });
  function start(){clearInterval(timer);refresh();timer=setInterval(refresh,15000);}
  document.addEventListener('kitchen-queue-changed',()=>{if(storeView)refresh();});
  function choose(value){
    selection=value;const url=new URL(location.href);if(value==='today')url.searchParams.delete('date');else url.searchParams.set('date',value);history.replaceState({},'',url);
    for(const c of cards.values()){Object.values(c.charts).forEach(chart=>chart.destroy());c.root.remove();}cards.clear();
    if(dateInput)dateInput.value=value==='today'?lastData?.latest_date||'':value;updateArrows();
    if(controller)controller.abort();status.hidden=false;status.className='notification is-light';status.textContent='Loading selected date…';document.getElementById('live-mode')?.classList.remove('live-mode-active');start();
  }
  if(dateInput)dateInput.addEventListener('change',()=>{if(dateInput.value)choose(dateInput.value===lastData?.latest_date?'today':dateInput.value);});
  if(todayButton)todayButton.addEventListener('click',()=>choose('today'));
  function updateArrows(){
    const value=dateInput?.value;
    if(previousButton)previousButton.disabled=!value||!dateInput.min||value<=dateInput.min;
    if(nextButton)nextButton.disabled=!value||!dateInput.max||value>=dateInput.max;
  }
  function step(days){
    if(!dateInput?.value)return;
    const day=new Date(dateInput.value+'T12:00:00Z');day.setUTCDate(day.getUTCDate()+days);
    const value=day.toISOString().slice(0,10);
    if(value<dateInput.min||value>dateInput.max)return;
    choose(value===lastData?.latest_date?'today':value);
  }
  previousButton?.addEventListener('click',()=>step(-1));
  nextButton?.addEventListener('click',()=>step(1));
  document.getElementById('live-refresh')?.addEventListener('click',start);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearInterval(timer);else start();});
  window.addEventListener('pageshow',start);window.addEventListener('pagehide',()=>clearInterval(timer));start();
})();
