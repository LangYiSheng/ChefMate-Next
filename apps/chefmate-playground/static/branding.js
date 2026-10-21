import { initAppearance } from './appearance.js';
export const BRAND_ICONS = Object.freeze([
  {id:'friendly-pot',name:'小锅搭子',note:'亲切一些，适合陪你一起做饭。'},
]);
export function iconPath() { return '/static/brand/friendly-pot.svg'; }
export function currentIcon() {
  return 'friendly-pot';
}
export function brandImage(className='brand-icon') {
  const image=document.createElement('img');image.src=iconPath(currentIcon());image.alt='';image.className=className;
  image.dataset.chefmateIcon='';image.setAttribute('aria-hidden','true');return image;
}
export function applyBranding(id=currentIcon()) {
  initAppearance();
  for (const image of document.querySelectorAll('[data-chefmate-icon]')) image.src=iconPath(id);
  const favicon=document.querySelector('link[rel="icon"]');if(favicon) favicon.href=iconPath(id);
}
