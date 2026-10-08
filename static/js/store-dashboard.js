(() => {
  'use strict';
  const feed=document.getElementById('store-order-feed'),dialog=document.getElementById('store-skus');
  if(!feed)return;
  let store=null,chart=null,fingerprint='';
  const paceCards=document.getElementById('store-pace-cards');
  const intakeData=document.getElementById('store-intake-data');
  const completionData=document.getElementById('store-completion-data');
  const waitData=document.getElementById('store-wait-data');
  let waiting=null,waitClock=0,waitObserved=performance.now(),waitUnavailable=false;
  function customerWait(){
    if(!waitData)return;waitData.replaceChildren();
    if(waitUnavailable){waitData.append(el('p','store-pace-note','Wait temporarily unavailable.'));return;}
    if(!waiting){waitData.append(el('strong','store-intake-value','—'),el('p','store-intake-detail','No active orders off hold'));return;}
    const seconds=Math.max(0,Math.floor((waitClock+performance.now()-waitObserved-Date.parse(waiting))/1000));
    waitData.append(el('strong','store-intake-value',Math.floor(seconds/60)+'m '+String(seconds%60).padStart(2,'0')+'s'),el('p','store-intake-detail','Oldest active · Holds excluded'));
  }
  document.addEventListener('kitchen-wait',event=>{waiting=event.detail.created_at;waitClock=Date.parse(event.detail.generated_at);waitObserved=performance.now();waitUnavailable=false;customerWait();});
  setInterval(()=>{if(!document.hidden)customerWait();},1000);
  let flow=null,flowUnavailable=false;
  function intake(){
    intakeData.replaceChildren();completionData.replaceChildren();
    if(!flow){for(const target of [intakeData,completionData])target.append(el('p','store-pace-note',flowUnavailable?'Flow temporarily unavailable.':'Collecting item flow…'));return;}
    const metric=flow.intake,number=new Intl.NumberFormat('en-US',{maximumFractionDigits:1}),minutes=flow.window_minutes;
    intakeData.append(el('strong','store-intake-value',metric.items_per_minute.toFixed(1)+' items/min'));
    intakeData.append(el('p','store-intake-detail',number.format(metric.recent_items)+' items · '+metric.recent_orders+' orders in the last '+minutes+' minutes'));
    completionData.append(el('strong','store-intake-value',flow.completion.items_per_minute.toFixed(1)+' items/min'));
    completionData.append(el('p','store-intake-detail',number.format(flow.completion.completed_items)+' items marked done in the last '+minutes+' minutes'));
    const labels={rising:'↑ Rising',steady:'→ Steady',falling:'↓ Falling'};
    const trend=el('p','store-intake-detail',(labels[metric.trend]||labels.steady)+' · Preceding 15 min: '+metric.baseline_items_per_minute.toFixed(1)+' items/min');
    trend.title='Rising or falling means more than 20% above or below the preceding 15-minute rate.';
    intakeData.append(trend);
  }
  document.addEventListener('kitchen-flow',event=>{flow=event.detail;flowUnavailable=false;intake();});
  document.addEventListener('kitchen-flow-unavailable',()=>{flow=null;flowUnavailable=true;waiting=null;waitUnavailable=true;intake();customerWait();});
  document.addEventListener('store-access-denied',()=>{flow=null;waiting=null;waitUnavailable=true;intakeData.replaceChildren();completionData.replaceChildren();waitData?.replaceChildren();});
  function pace(){
    paceCards.replaceChildren();
    if(!store||store.setup_required||store.loading){paceCards.append(el('p','store-pace-note',store?.setup_required?'Store setup required.':'Collecting order intervals…'));return;}
    const duration=value=>value===null||value===undefined?'—':value<60?Math.round(value)+' sec':Math.floor(value/60)+'m '+Math.round(value%60)+'s';
    for(const size of [1,2,3]){
      const metric=(store.order_pace||[]).find(item=>item.items===size)||{};
      const card=el('div','store-pace-card');
      card.append(el('strong','',size+(size===1?' item':' items')),el('span','store-pace-value',duration(metric.recent_seconds)),el('small','',metric.recent_samples?metric.recent_samples+' recent intervals':'No recent intervals'),el('small','','Fast today: '+duration(metric.fast_seconds)));
      paceCards.append(card);
    }
  }
  document.addEventListener('store-access-denied',()=>{paceCards.replaceChildren();});
  document.addEventListener('store-access-denied',()=>{store=null;fingerprint='';feed.replaceChildren();if(dialog.open)dialog.close();if(chart){chart.destroy();chart=null;}document.getElementById('store-sku-rows').replaceChildren();});
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const money=(cents,currency='USD')=>{try{return new Intl.NumberFormat('en-US',{style:'currency',currency}).format(cents/100);}catch{return currency+' '+(cents/100).toFixed(2);}};
  function products(){
    if(!store)return;
    const rows=document.getElementById('store-sku-rows'),counts=store.sku_counts||[];rows.replaceChildren();
    for(const item of counts){const row=el('tr');row.append(el('td','',item.sku),el('td','',item.quantity));rows.append(row);}
    if(!counts.length){const row=el('tr'),cell=el('td','','No items sold today.');cell.colSpan=2;row.append(cell);rows.append(row);}
    const canvas=document.getElementById('store-sku-canvas');canvas.parentElement.style.height=Math.max(160,counts.length*28)+'px';canvas.setAttribute('aria-label',counts.map(i=>i.sku+': '+i.quantity).join('; ')||'No items sold today');
    if(!window.Chart)return;
    if(chart){chart.data.labels=counts.map(i=>i.sku);chart.data.datasets[0].data=counts.map(i=>i.quantity);chart.update('none');return;}
    chart=new Chart(canvas,{type:'bar',data:{labels:counts.map(i=>i.sku),datasets:[{label:'Items',data:counts.map(i=>i.quantity),backgroundColor:'#16835f',borderRadius:3}]},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:false}},scales:{x:{beginAtZero:true,ticks:{precision:0},grid:{color:'#dbe4e8'}}}}});
  }
  document.addEventListener('store-data',event=>{
    store=event.detail.stores?.[0];pace();intake();if(!store)return;
    if(store.setup_required||store.loading){if(feed.dataset.kitchen!=='true')feed.replaceChildren(el('p','',store.setup_required?store.message:'Collecting today’s orders…'));fingerprint='';return;}
    const orders=store.latest_orders||[],next=JSON.stringify(orders);
    if(feed.dataset.kitchen!=='true'&&next!==fingerprint){fingerprint=next;feed.replaceChildren();
      for(const order of orders){const root=el('article','store-order'),head=el('div','store-order-head');
        const time=new Intl.DateTimeFormat('en-US',{timeZone:store.timezone,hour:'numeric',minute:'2-digit',second:'2-digit'}).format(new Date(order.created_at));
        head.append(el('strong','','Order '+order.number),el('small','',time),el('strong','',money(order.sales_cents,order.currency)));root.append(head);
        root.append(el('small','',order.status+' · Tips '+money(order.tips_cents,order.currency)));
        const table=el('table');table.setAttribute('aria-label','Order '+order.number+' items');
        const thead=el('thead'),labels=el('tr');['Item','Qty','SKU / Status'].forEach(t=>labels.append(el('th','is-size-7',t)));thead.append(labels);table.append(thead);
        const body=el('tbody');for(const item of order.items){const row=el('tr');row.append(el('td','',item.name||item.sku||'Unnamed item'),el('td','',item.quantity),el('td','',item.sku+' · '+item.status));body.append(row);}table.append(body);root.append(table);feed.append(root);
      }
      if(!orders.length)feed.append(el('p','live-empty','No orders today.'));
    }
    if(dialog.open)products();
  });
  document.addEventListener('store-products',()=>{dialog.showModal();products();});
  document.getElementById('store-skus-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
  const fullscreen=document.getElementById('store-fullscreen');
  if(!document.fullscreenEnabled)fullscreen.hidden=true;
  fullscreen.addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{fullscreen.textContent='Use browser full screen';}});
  document.addEventListener('fullscreenchange',()=>{fullscreen.textContent=document.fullscreenElement?'Exit full screen':'Full screen';});
})();
