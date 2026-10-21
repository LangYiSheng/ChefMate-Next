export function panelBounds(available, {minimum=340,maximum=760,remaining=400}={}) {
  const max=Math.max(minimum,Math.min(maximum,Math.floor(available-remaining)));
  return {min:minimum,max};
}
export function panelWidth(preferred,bounds) {
  return Math.round(Math.min(bounds.max,Math.max(bounds.min,Number.isFinite(preferred)?preferred:460)));
}

// A right-hand pane grows to the left. The same controller serves chat and
// center inspectors, and releases all listeners when an inspector is removed.
export function resizeRightPane({root,handle,key,property='--detail-width',bounds,enabled=()=>true,initial=460,observe=[]}) {
  let preferred=initial,drag=null;
  try {const saved=Number(localStorage.getItem(key));if(saved>0)preferred=saved;}catch{}
  const paint=()=>{const range=bounds(),width=panelWidth(preferred,range);root.style.setProperty(property,`${width}px`);handle.setAttribute('aria-valuemin',String(range.min));handle.setAttribute('aria-valuemax',String(range.max));handle.setAttribute('aria-valuenow',String(width));return width;};
  const persist=()=>{try{localStorage.setItem(key,String(preferred));}catch{}};
  const stop=()=>{if(!drag)return;drag=null;root.classList.remove('pane-resizing');persist();};
  const down=event=>{if(event.button!==0 || !enabled())return;drag={x:event.clientX,width:paint()};handle.setPointerCapture(event.pointerId);root.classList.add('pane-resizing');event.preventDefault();};
  const move=event=>{if(!drag)return;preferred=panelWidth(drag.width+drag.x-event.clientX,bounds());paint();};
  const keydown=event=>{if(!enabled())return;const step=event.shiftKey?48:16,change={ArrowLeft:step,ArrowRight:-step}[event.key];if(!change && !['Home','End'].includes(event.key))return;event.preventDefault();const range=bounds();preferred=event.key==='Home'?range.min:event.key==='End'?range.max:panelWidth(paint()+change,range);paint();persist();};
  handle.addEventListener('pointerdown',down);handle.addEventListener('pointermove',move);handle.addEventListener('keydown',keydown);
  for(const type of ['pointerup','pointercancel','lostpointercapture'])handle.addEventListener(type,stop);
  const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(paint);observer?.observe(root);for(const element of observe)observer?.observe(element);window.addEventListener('resize',paint);paint();
  return {paint,dispose(){stop();observer?.disconnect();window.removeEventListener('resize',paint);handle.removeEventListener('pointerdown',down);handle.removeEventListener('pointermove',move);handle.removeEventListener('keydown',keydown);for(const type of ['pointerup','pointercancel','lostpointercapture'])handle.removeEventListener(type,stop);}};
}
