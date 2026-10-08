(() => {
  const scroller=document.querySelector('.board-orders'),fullscreen=document.getElementById('store-fullscreen');
  fullscreen.hidden=!document.fullscreenEnabled;
  fullscreen.addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{fullscreen.textContent='Use browser full screen';}});
  document.addEventListener('fullscreenchange',()=>fullscreen.textContent=document.fullscreenElement?'Exit full screen':'Full screen');
  window.addEventListener('pagehide',()=>document.body.style.visibility='hidden');
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  const feed=document.getElementById('store-order-feed');
  const dialog=document.getElementById('timer-dialog');
  if(dialog){
    let profile={green_seconds:0,yellow_seconds:300,red_seconds:600};
    document.addEventListener('kitchen-timer-profile',event=>profile=event.detail);
    document.getElementById('timer-settings').addEventListener('click',()=>{for(const color of ['green','yellow','red'])document.getElementById('timer-'+color).value=profile[color+'_seconds']/60;document.getElementById('timer-message').textContent='';dialog.showModal();});
    document.getElementById('timer-cancel').addEventListener('click',()=>dialog.close());
    document.getElementById('timer-profile-form').addEventListener('submit',async event=>{
      event.preventDefault();const button=document.getElementById('timer-save'),message=document.getElementById('timer-message');
      const values=Object.fromEntries(['green','yellow','red'].map(c=>[c+'_seconds',Math.round(Number(document.getElementById('timer-'+c).value)*60)]));
      if(!(values.green_seconds<values.yellow_seconds&&values.yellow_seconds<values.red_seconds)){message.textContent='Thresholds must increase: green, yellow, then red.';return;}
      button.disabled=true;
      try{const response=await fetch('/dashboard/stores/'+feed.dataset.storeId+'/kitchen-profile',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Kitchen-CSRF':feed.dataset.csrf},body:JSON.stringify(values)});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Profile could not be saved.');profile=data;dialog.close();document.getElementById('live-refresh').click();}
      catch(error){message.textContent=error.message;}finally{button.disabled=false;}
    });
  }
})();
