(() => {
  const feed=document.getElementById('store-order-feed');
  if(!feed)return;
  if(feed.dataset.test!=='true')return;
  const stream=document.getElementById('test-stream'),interval=document.getElementById('test-interval'),message=document.getElementById('test-message');
  let timer=null,busy=false;
  function stop(){clearTimeout(timer);timer=null;stream.setAttribute('aria-pressed','false');stream.textContent='Start stream';}
  async function send(action,count=1,scenario='mixed'){
    if(busy)return false;
    busy=true;document.querySelectorAll('[data-test-count],#test-clear').forEach(b=>b.disabled=true);
    try{
      const response=await fetch('/dashboard/stores/'+feed.dataset.storeId+'/kitchen-test/orders',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Kitchen-CSRF':feed.dataset.csrf},body:JSON.stringify({action,count,scenario})});
      if(!response.ok){let data=await response.json().catch(()=>({}));throw new Error(data.detail||'Test orders could not be saved.');}
      message.textContent=action==='clear'?'Test queue cleared.':count+' sample order'+(count===1?'':'s')+' added.';
      document.getElementById('live-refresh').click();return true;
    }catch(error){stop();message.textContent=error.message;return false;}
    finally{busy=false;document.querySelectorAll('[data-test-count],#test-clear').forEach(b=>b.disabled=false);}
  }
  document.querySelectorAll('[data-test-count]').forEach(b=>b.addEventListener('click',()=>send('add',Number(b.dataset.testCount),b.dataset.testScenario||'mixed')));
  document.getElementById('test-clear').addEventListener('click',()=>{if(confirm('Clear all sample orders for this store? Real orders are unaffected.')){stop();send('clear');}});
  async function next(){if(stream.getAttribute('aria-pressed')!=='true'||document.hidden)return;if(await send('add')){if(stream.getAttribute('aria-pressed')==='true')timer=setTimeout(next,Number(interval.value)*1000);}}
  stream.addEventListener('click',()=>{if(stream.getAttribute('aria-pressed')==='true')stop();else{stream.setAttribute('aria-pressed','true');stream.textContent='Stop stream';next();}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('pagehide',stop);
})();
