(() => {
  const scroller=document.querySelector('.board-orders'),fullscreen=document.getElementById('store-fullscreen');
  for(const [id,direction] of [['board-left',-1],['board-right',1]])document.getElementById(id).addEventListener('click',()=>scroller.scrollBy({left:direction*Math.max(300,scroller.clientWidth*.8),behavior:'smooth'}));
  fullscreen.hidden=!document.fullscreenEnabled;
  fullscreen.addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{fullscreen.textContent='Use browser full screen';}});
  document.addEventListener('fullscreenchange',()=>fullscreen.textContent=document.fullscreenElement?'Exit full screen':'Full screen');
  window.addEventListener('pagehide',()=>document.body.style.visibility='hidden');
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
})();
