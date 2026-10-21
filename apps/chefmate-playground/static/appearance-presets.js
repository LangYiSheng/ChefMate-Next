// Shared by the application, color cards and preview.
const palette=(id,name,mode,colors)=>Object.freeze({id,name,mode,...colors});
export const PALETTES=Object.freeze([
  palette('ink','暖白墨黑','light',{background:'#f2f1ee',canvas:'#fefdfb',surface:'#f6f5f1',soft:'#eae9e5',border:'#deddd7',text:'#2b2c29',muted:'#686960',accent:'#343d38',accentHover:'#202923',onAccent:'#ffffff'}),
  palette('sage','鼠尾草绿','light',{background:'#f3f5f2',canvas:'#ffffff',surface:'#f7f9f6',soft:'#edf2ed',border:'#dce4dc',text:'#25372b',muted:'#5d6f63',accent:'#3f6b50',accentHover:'#2d533c',onAccent:'#ffffff'}),
  palette('oat','燕麦奶咖','light',{background:'#f3efe8',canvas:'#fffdf8',surface:'#f8f4ed',soft:'#f0e8db',border:'#e4dacb',text:'#3b342c',muted:'#756453',accent:'#795c3e',accentHover:'#62472e',onAccent:'#ffffff'}),
  palette('clay','暖陶杏橙','light',{background:'#f6efe9',canvas:'#fffcf8',surface:'#fbf4ee',soft:'#f6e7dd',border:'#e8d8cc',text:'#3c312b',muted:'#7b6354',accent:'#a34f31',accentHover:'#843b23',onAccent:'#ffffff'}),
  palette('blue','雾蓝石灰','light',{background:'#f0f3f6',canvas:'#ffffff',surface:'#f5f8fb',soft:'#e7eef5',border:'#d9e1e9',text:'#273541',muted:'#596d7e',accent:'#3e6687',accentHover:'#2c4d69',onAccent:'#ffffff'}),
  palette('rose','莲雾浅粉','light',{background:'#f7f0f1',canvas:'#fffcfc',surface:'#fcf5f6',soft:'#f3e5e9',border:'#e7d9dd',text:'#3d3036',muted:'#795f6a',accent:'#91556b',accentHover:'#744055',onAccent:'#ffffff'}),
  palette('lavender','淡紫云雾','light',{background:'#f2f0f6',canvas:'#fefcfe',surface:'#f7f4fb',soft:'#ece6f5',border:'#ded8e8',text:'#342e40',muted:'#70617f',accent:'#70578d',accentHover:'#56416e',onAccent:'#ffffff'}),
  palette('olive','青柠橄榄','light',{background:'#f1f3e9',canvas:'#fefef8',surface:'#f7f8ef',soft:'#e9eed9',border:'#dce1cd',text:'#32382a',muted:'#5c664a',accent:'#596e36',accentHover:'#435426',onAccent:'#ffffff'}),
  palette('graphite','午夜石墨','dark',{background:'#191b1c',canvas:'#232627',surface:'#2b2f30',soft:'#353b3a',border:'#444b49',text:'#f0f1ed',muted:'#b7c0ba',accent:'#c9d5cb',accentHover:'#e0e8e1',onAccent:'#202622'}),
  palette('night','深夜松绿','dark',{background:'#14201b',canvas:'#1c2a23',surface:'#223329',soft:'#2a3d31',border:'#3d5143',text:'#eef4ee',muted:'#b3c5b8',accent:'#b1d4b9',accentHover:'#cbe5d0',onAccent:'#16251b'}),
  palette('deep-blue','深海雾蓝','dark',{background:'#151e29',canvas:'#1e2a38',surface:'#253344',soft:'#2f4054',border:'#40536a',text:'#edf3fb',muted:'#b4c4d8',accent:'#a9c9ee',accentHover:'#c6def8',onAccent:'#17283c'}),
  palette('cocoa','可可炭棕','dark',{background:'#241c19',canvas:'#302621',surface:'#392e27',soft:'#49392e',border:'#604b3e',text:'#f7eee5',muted:'#cdb9a7',accent:'#dfbd94',accentHover:'#efd3b1',onAccent:'#302216'}),
]);
const sans='"ChefMate Sans", "PingFang SC", "Microsoft YaHei", sans-serif';
const serif='"ChefMate Serif", "Songti SC", "SimSun", serif';
const wenkai='"ChefMate WenKai", "KaiTi", serif';
export const FONT_PAIRINGS=Object.freeze([
  Object.freeze({id:'mixed',name:'宋黑搭配',note:'加粗宋体标题，黑体正文与数值',title:serif,titleWeight:700,reading:sans,numeric:sans,ui:sans}),
  Object.freeze({id:'sans',name:'清晰黑体',note:'统一黑体，清楚利落',title:sans,titleWeight:600,reading:sans,numeric:sans,ui:sans}),
  Object.freeze({id:'serif',name:'书卷宋体',note:'宋体标题与正文，黑体数值',title:serif,titleWeight:600,reading:serif,numeric:sans,ui:sans}),
  Object.freeze({id:'wenkai',name:'温柔文楷',note:'文楷标题与正文，黑体数值',title:wenkai,titleWeight:400,reading:wenkai,numeric:sans,ui:sans}),
]);
export const DEFAULT_APPEARANCE=Object.freeze({theme:'light',light_palette:'ink',dark_palette:'graphite',font_pairing:'mixed',show_estimates:true});
export const THEME_MODES=Object.freeze(['system','light','dark']);
export function normalizeAppearance(value) {
  const saved=value && typeof value==='object' && !Array.isArray(value)?value:{};
  return {...saved,theme:THEME_MODES.includes(saved.theme)?saved.theme:DEFAULT_APPEARANCE.theme,
    light_palette:PALETTES.some(p=>p.id===saved.light_palette && p.mode==='light')?saved.light_palette:DEFAULT_APPEARANCE.light_palette,
    dark_palette:PALETTES.some(p=>p.id===saved.dark_palette && p.mode==='dark')?saved.dark_palette:DEFAULT_APPEARANCE.dark_palette,
    font_pairing:FONT_PAIRINGS.some(f=>f.id===saved.font_pairing)?saved.font_pairing:DEFAULT_APPEARANCE.font_pairing,
    show_estimates:typeof saved.show_estimates==='boolean'?saved.show_estimates:true};
}
export function resolveTheme(mode,darkSystem=false) { return mode==='dark' || mode==='system' && darkSystem?'dark':'light'; }
export function resolveAppearance(value,darkSystem=false) {
  const preferences=normalizeAppearance(value),theme=resolveTheme(preferences.theme,darkSystem);
  return {preferences,theme,palette:PALETTES.find(p=>p.id===preferences[`${theme}_palette`]),fonts:FONT_PAIRINGS.find(f=>f.id===preferences.font_pairing)};
}
export function appearanceTokens(palette,fonts) {
  const dark=palette.mode==='dark';
  return {'--color-background':palette.background,'--color-canvas':palette.canvas,'--color-surface':palette.surface,
    '--color-hover':palette.soft,'--color-border':palette.border,'--color-border-strong':palette.muted,
    '--color-text':palette.text,'--color-muted':palette.muted,'--color-subtle':palette.muted,
    '--color-accent':palette.accent,'--color-accent-hover':palette.accentHover,'--color-accent-soft':palette.soft,
    '--color-on-accent':palette.onAccent,'--color-success':dark?'#a5d8b3':'#356b45',
    '--color-warning':dark?'#e7c48d':'#816024','--color-warning-soft':dark?'#3c3425':'#fbf7ef',
    '--color-danger':dark?'#f0aca2':'#a34138','--color-danger-soft':dark?'#432f2b':'#fcf2ef',
    '--color-overlay':dark?'#050c08b3':'#17281e80','--color-shadow':dark?'#00000033':'#17281e12',
    '--font-ui':fonts.ui,'--font-title':fonts.title,'--font-title-weight':String(fonts.titleWeight),'--font-reading':fonts.reading,'--font-numeric':fonts.numeric};
}
