import test from 'node:test';
import assert from 'node:assert/strict';
import {PALETTES,FONT_PAIRINGS,DEFAULT_APPEARANCE,normalizeAppearance,resolveAppearance,appearanceTokens} from '../appearance-presets.js';
import {applyAppearance,initAppearance,readAppearance} from '../appearance.js';

function luminance(hex) {
  const rgb=hex.slice(1).match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);
  return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
}
function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}

test('all palettes keep ordinary text, secondary text and primary actions readable',()=>{
  assert.equal(new Set(PALETTES.map(p=>p.id)).size,12);
  assert.equal(PALETTES.filter(p=>p.mode==='light').length,8);
  assert.equal(PALETTES.filter(p=>p.mode==='dark').length,4);
  for(const p of PALETTES) {
    for(const background of [p.canvas,p.background,p.surface,p.soft]) {
      assert.ok(contrast(p.text,background)>=4.5,`${p.id}: primary text on ${background}`);
      assert.ok(contrast(p.muted,background)>=4.5,`${p.id}: secondary text on ${background}`);
    }
    assert.ok(contrast(p.onAccent,p.accent)>=4.5,`${p.id}: primary action`);
    assert.ok(contrast(p.onAccent,p.accentHover)>=4.5,`${p.id}: hovered primary action`);
    assert.ok(contrast(p.accent,p.canvas)>=4.5,`${p.id}: links`);
  }
});

test('fresh and malformed preferences use warm ink and mixed fonts, retaining unrelated settings',()=>{
  for(const value of [undefined,null,[],3,'dark',{}])assert.deepEqual(normalizeAppearance(value),DEFAULT_APPEARANCE);
  const result=normalizeAppearance({theme:'unknown',light_palette:'night',dark_palette:'oat',font_pairing:'missing',density:'compact'});
  assert.deepEqual(result,{...DEFAULT_APPEARANCE,density:'compact'});
  assert.deepEqual(readAppearance({getItem:()=>'{"theme":"dark","dark_palette":"deep-blue","font_pairing":"serif"}'}),
    {...DEFAULT_APPEARANCE,theme:'dark',dark_palette:'deep-blue',font_pairing:'serif'});
});

test('automatic appearance changes palette with the OS, while explicit modes keep their selection',()=>{
  const value={theme:'system',light_palette:'clay',dark_palette:'cocoa',font_pairing:'wenkai'};
  assert.equal(resolveAppearance(value,false).palette.id,'clay');assert.equal(resolveAppearance(value,true).palette.id,'cocoa');
  assert.equal(resolveAppearance({...value,theme:'light'},true).palette.id,'clay');
  assert.equal(resolveAppearance({...value,theme:'dark'},false).palette.id,'cocoa');
  assert.equal(resolveAppearance(value,true).fonts.id,'wenkai');
});

test('font pairings keep controls and numbers sans while allowing independent reading and title roles',()=>{
  const mixed=FONT_PAIRINGS.find(f=>f.id==='mixed'),serif=FONT_PAIRINGS.find(f=>f.id==='serif');
  assert.notEqual(mixed.title,mixed.reading);assert.equal(mixed.numeric,mixed.ui);
  assert.equal(mixed.titleWeight,700);
  assert.equal(serif.title,serif.reading);assert.notEqual(serif.reading,serif.numeric);
  for(const p of PALETTES)for(const f of FONT_PAIRINGS) {
    const tokens=appearanceTokens(p,f);
    assert.equal(tokens['--color-on-accent'],p.onAccent);
    assert.equal(tokens['--font-title'],f.title);assert.equal(tokens['--font-reading'],f.reading);
    assert.ok(Object.values(tokens).every(value=>typeof value==='string' && value.length));
  }
});

test('application updates all roles, browser chrome and cross-window/system changes from saved preferences',t=>{
  const previous=Object.fromEntries(['document','window','matchMedia','localStorage'].map(key=>[key,globalThis[key]]));
  t.after(()=>{for(const [key,value]of Object.entries(previous))value===undefined?delete globalThis[key]:globalThis[key]=value;});
  const tokens=new Map(),meta={},dataset={},events={},media={matches:false,addEventListener(name,fn){this[name]=fn;}};
  const storage={value:JSON.stringify({...DEFAULT_APPEARANCE,theme:'system',dark_palette:'deep-blue'}),getItem(){return this.value;}};
  globalThis.localStorage=storage;globalThis.matchMedia=()=>media;
  globalThis.document={documentElement:{dataset,style:{setProperty:(key,value)=>tokens.set(key,value)}},querySelector:()=>meta};
  globalThis.window={matchMedia:()=>media,addEventListener:(name,fn)=>events[name]=fn};
  initAppearance();assert.equal(dataset.palette,'ink');assert.equal(dataset.fontPairing,'mixed');
  media.matches=true;media.change();assert.equal(dataset.palette,'deep-blue');assert.equal(meta.content,'#151e29');
  storage.value=JSON.stringify({...DEFAULT_APPEARANCE,light_palette:'rose',font_pairing:'wenkai'});
  events.storage({key:'chefmate:ui-settings'});assert.equal(dataset.palette,'rose');assert.equal(dataset.fontPairing,'wenkai');
  assert.match(tokens.get('--font-title'),/WenKai/);
  storage.value=null;events.storage({key:null});assert.equal(dataset.palette,'ink');
  applyAppearance({...DEFAULT_APPEARANCE,theme:'dark',dark_palette:'night'});assert.equal(dataset.palette,'night');
  assert.equal(tokens.get('--color-on-accent'),'#16251b');assert.equal(tokens.get('--color-accent'),'#b1d4b9');
});
