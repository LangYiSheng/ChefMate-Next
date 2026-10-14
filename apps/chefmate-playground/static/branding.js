export const BRAND_ICONS = Object.freeze([
  {id:'pot-c',name:'锅边的 C',note:'简洁，有 ChefMate 的字母记忆点。'},
  {id:'chef-hat',name:'厨帽',note:'一眼认出厨房助手，小尺寸也清楚。'},
  {id:'leaf-bowl',name:'碗与嫩叶',note:'轻盈、自然，偏日常饮食的感觉。'},
  {id:'friendly-pot',name:'小锅搭子',note:'亲切一些，适合陪你一起做饭。'},
]);
export function iconPath(id) { return `/static/brand/${BRAND_ICONS.some(icon=>icon.id===id) ? id : 'pot-c'}.svg`; }
export function currentIcon() {
  try { return JSON.parse(localStorage.getItem('chefmate:ui-settings') || '{}').brand_icon || 'pot-c'; }
  catch { return 'pot-c'; }
}
export function brandImage(className='brand-icon') {
  const image=document.createElement('img');image.src=iconPath(currentIcon());image.alt='';image.className=className;
  image.dataset.chefmateIcon='';image.setAttribute('aria-hidden','true');return image;
}
export function applyBranding(id=currentIcon()) {
  for (const image of document.querySelectorAll('[data-chefmate-icon]')) image.src=iconPath(id);
  const favicon=document.querySelector('link[rel="icon"]');if(favicon) favicon.href=iconPath(id);
}
