(() => {
  'use strict';
  const grid = document.getElementById('live-stores');
  if (!grid) return;
  const status = document.getElementById('live-status');
  const refreshed = document.getElementById('live-refreshed');
  const enable = document.getElementById('enable-live-webhook');
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
    info.append(name,date);header.append(info,badge);root.append(header);
    const message=el('p','live-empty');root.append(message);
    const body=el('div'),kpis=el('div','live-kpis');
    const fields={};
    for (const [key,label] of [['sales','Sales today'],['tips','Tips today'],['orders','Orders today']]) {
      const cell=el('div');fields[key]=el('strong');cell.append(el('small','',label),fields[key]);kpis.append(cell);
    }
    body.append(kpis);
    const now=el('div','live-now'), recent=el('strong'),pace=el('p','live-pace'),last=el('p','live-last');
    now.append(el('small','','LAST 5 MINUTES'),recent,pace,last);body.append(now);
    const warning=el('p','live-warning');body.append(warning);
    const details=el('details'),summary=el('summary','','Hourly sales & category totals');details.append(summary);
    const avg=el('p','is-size-7 mt-2');details.append(avg);
    const hourlyWrap=el('div','live-chart'),hourly=el('canvas');hourly.setAttribute('role','img');hourlyWrap.append(hourly);
    details.append(el('p','live-chart-title','Hourly net sales'),hourlyWrap);
    const activityWrap=el('div','live-chart live-chart-small'),activity=el('canvas');activity.setAttribute('role','img');activityWrap.append(activity);
    details.append(el('p','live-chart-title','Orders per 5 minutes · past hour'),activityWrap);
    const table=el('table','live-category'),head=el('thead'),tr=el('tr');
    ['Category','Qty','Sales','Share'].forEach(label=>tr.append(el('th','',label)));head.append(tr);
    const rows=el('tbody');table.append(head,rows);details.append(table);body.append(details);root.append(body);grid.append(root);
    const card={root,name,date,badge,message,body,fields,recent,pace,last,warning,details,avg,rows,hourly,activity,charts:{}};
    details.addEventListener('toggle',()=>{if(details.open && card.data)drawCharts(card,card.data);});
    cards.set(id,card);return card;
  }
  function chart(card,key,canvas,points,isMoney) {
    if(!window.Chart){canvas.parentElement.textContent='Chart unavailable. Refresh when the chart library is accessible.';return;}
    const labels=points.map(p=>p.label+(p.partial?' · partial':'')),values=points.map(p=>isMoney?p.sales_cents/100:p.orders);
    canvas.setAttribute('aria-label',points.map((p,i)=>labels[i]+': '+(isMoney?money(p.sales_cents):p.orders+' orders')).join('; '));
    if(card.charts[key]){card.charts[key].data.labels=labels;card.charts[key].data.datasets[0].data=values;card.charts[key].update('none');return;}
    card.charts[key]=new Chart(canvas,{type:'bar',data:{labels,datasets:[{label:isMoney?'Net sales':'Orders',data:values,
      backgroundColor:isMoney?'#238b83':'#7ba8c4',borderRadius:3}]},options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>isMoney?money(Math.round(ctx.parsed.y*100)):ctx.parsed.y+' orders'}}},
      scales:{y:{beginAtZero:true,ticks:{precision:0,callback:value=>isMoney?'$'+value:value}},x:{ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:8}}}}});
  }
  function drawCharts(card,s){chart(card,'hourly',card.hourly,s.hourly,true);chart(card,'activity',card.activity,s.activity,false);}
  function render(data) {
    const alive=new Set();
    for(const s of data.stores){
      alive.add(s.id);const c=cards.get(s.id)||makeCard(s.id);c.name.textContent=s.name;
      c.message.hidden=true;c.body.hidden=false;c.badge.textContent='';
      if(s.setup_required){c.date.textContent='Setup needed';c.body.hidden=true;c.message.hidden=false;c.message.textContent=s.message;continue;}
      c.data=s;c.date.textContent=s.date+' · '+s.timezone;
      c.badge.textContent=data.stale?'Awaiting sync':s.order_count?'Trading today':s.currency_warning?'No USD sales today':'No sales today';
      if(data.initializing){c.body.hidden=true;c.message.hidden=false;c.message.textContent='Collecting today’s orders…';continue;}
      if(!c.initialized){c.details.open=!!s.order_count;c.initialized=true;}
      if(!s.order_count){c.message.hidden=false;c.message.textContent=s.currency_warning?'No USD sales today. Other currencies are excluded.':'No sales today.';c.body.hidden=!s.recent_orders&&!s.currency_warning;}
      c.fields.sales.textContent=money(s.sales_cents);c.fields.tips.textContent=money(s.tips_cents);c.fields.orders.textContent=s.order_count;
      c.recent.textContent=s.recent_orders+' orders · '+money(s.recent_sales_cents);
      c.pace.textContent=s.pace+(s.pace_ratio!==null?' · '+s.pace_ratio.toFixed(1)+'× recent average':'');
      c.last.textContent=ago(s.last_sale_at,data.generated_at);
      c.avg.textContent='Average sale: '+money(s.average_sale_cents)+' · Current hour is partial.';
      c.warning.hidden=!s.currency_warning;c.warning.textContent='Non-USD orders are excluded from these totals.';
      c.rows.replaceChildren();
      for(const row of s.categories){const tr=el('tr');[row.name,row.quantity,money(row.sales_cents),row.share+'%'].forEach(v=>tr.append(el('td','',v)));c.rows.append(tr);}
      if(!s.categories.length){const tr=el('tr'),td=el('td','','No items sold today');td.colSpan=4;tr.append(td);c.rows.append(tr);}
      if(c.details.open)drawCharts(c,s);
    }
    for(const [id,c]of cards){if(!alive.has(id)){Object.values(c.charts).forEach(chart=>chart.destroy());c.root.remove();cards.delete(id);}}
    let text;
    if(!data.connected)text='Connect Poynt to see today’s activity.';
    else if(!data.stores.length)text='Add or activate stores in Store Settings to start tracking today.';
    else if(data.error)text=data.error+' Displayed totals may be out of date.';
    else if(data.queue_delayed)text='Order updates are delayed. Displayed totals may be out of date.';
    else if(data.initializing)text='Collecting today’s orders. This can take a moment on the first visit.';
    else if(data.stale)text='Waiting for an order sync. Displayed totals may be out of date.';
    else text=(data.webhook_registered?'Order webhooks enabled.':'Periodic sync active. Enable order webhooks for faster updates.')+' Last full sync: '+
      new Date(data.last_reconciled_at).toLocaleTimeString();
    status.textContent=text;status.className='notification '+((data.error||data.stale||data.queue_delayed)?'is-warning is-light':'is-light');
    refreshed.textContent='Page refreshed '+new Date(data.generated_at).toLocaleTimeString();
    if(enable)enable.hidden=!data.can_enable_webhook||data.webhook_registered;
  }
  async function refresh() {
    if(inFlight||document.hidden)return;
    inFlight=true;
    try {
      const response=await fetch('/dashboard/data',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000)});
      if(response.status===401){location.assign('/login');return;}
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Dashboard could not refresh.');render(data);
    }catch(error){status.textContent=error.message+' Displayed totals may be out of date. Retrying automatically.';status.className='notification is-warning is-light';}
    finally{inFlight=false;}
  }
  if(enable)enable.addEventListener('click',async()=>{
    enable.disabled=true;enable.classList.add('is-loading');
    try{const response=await fetch('/dashboard/webhook/enable',{method:'POST',credentials:'same-origin',headers:{'X-Requested-With':'FoodTruckWorks'},signal:AbortSignal.timeout(45000)});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not enable webhooks.');await refresh();
    }catch(error){status.textContent=error.message;status.className='notification is-warning is-light';}
    finally{enable.disabled=false;enable.classList.remove('is-loading');}
  });
  function start(){clearInterval(timer);refresh();timer=setInterval(refresh,15000);}
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearInterval(timer);else start();});
  window.addEventListener('pageshow',start);window.addEventListener('pagehide',()=>clearInterval(timer));start();
})();
