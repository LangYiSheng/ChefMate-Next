import { initAppearance } from './appearance.js';
let faviconSource;
let watching=false;
async function updateFavicon() {
  try {
    faviconSource ||= fetch(iconPath()).then(response=>{if(!response.ok)throw new Error('brand unavailable');return response.text();});
    const source=await faviconSource;
    const accent=getComputedStyle(document.documentElement).getPropertyValue('--color-accent').trim();
    const favicon=document.querySelector('link[rel="icon"]');
    if(favicon)favicon.href=`data:image/svg+xml,${encodeURIComponent(source.replace('color="#343d38"',`color="${accent}"`))}`;
  }catch{faviconSource=null;}
}
export const BRAND_ICONS = Object.freeze([
  {id:'friendly-pot',name:'小锅搭子',note:'亲切一些，适合陪你一起做饭。'},
]);
export function iconPath() { return '/static/brand/chef-buddy.svg'; }
export function currentIcon() {
  return 'friendly-pot';
}
export function brandImage(className='brand-icon') {
  const image=document.createElement('span');image.className=`chefmate-mark ${className}`;
  image.dataset.chefmateIcon='';image.setAttribute('aria-hidden','true');return image;
}
export function applyBranding(id=currentIcon()) {
  if(!watching){window.addEventListener('chefmate:appearance',updateFavicon);watching=true;}
  initAppearance();
  for (const image of document.querySelectorAll('[data-chefmate-icon]')) {
    if(image.tagName==='IMG'){const mark=brandImage(image.className);image.replaceWith(mark);}
    else image.classList.add('chefmate-mark');
  }
  void updateFavicon();
}
