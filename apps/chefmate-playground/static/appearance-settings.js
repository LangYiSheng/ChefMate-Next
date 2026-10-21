import {node} from './ui-components.js';
import {icon} from './icons.js';
import {PALETTES,FONT_PAIRINGS,resolveAppearance,appearanceTokens} from './appearance-presets.js';

function setTokens(element,palette,fonts) {
  for(const [key,value]of Object.entries(appearanceTokens(palette,fonts)))element.style.setProperty(key,value);
  element.style.colorScheme=palette.mode;
}

export class AppearanceSettings {
  constructor({host,values,onChange,scope=globalThis}) {
    this.host=host;this.values=values;this.onChange=onChange;
    this.media=scope.matchMedia?.('(prefers-color-scheme: dark)');
    this.wide=scope.matchMedia?.('(min-width:1001px)');
    this.onSystemChange=()=>this.update();this.media?.addEventListener('change',this.onSystemChange);
    this.onWidthChange=()=>{this.previewArea.open=this.wide?.matches===true;};
    this.wide?.addEventListener('change',this.onWidthChange);
    this.build();this.update();
  }
  build() {
    this.host.className='appearance-layout';
    this.options=node('div','appearance-options');
    for(const mode of ['light','dark']) {
      const group=node('fieldset','palette-group');group.dataset.paletteMode=mode;
      group.append(node('legend','appearance-label',mode==='light'?'浅色配色':'暗色配色'));
      const choices=node('div','palette-grid');
      for(const palette of PALETTES.filter(item=>item.mode===mode)) {
        const label=node('label','palette-choice'),input=node('input');
        input.type='radio';input.name=`${mode}_palette`;input.value=palette.id;
        input.addEventListener('change',()=>{if(input.checked){this.values[`${mode}_palette`]=palette.id;this.previewMode=mode;this.update();this.onChange();}});
        const swatch=node('span','palette-swatch');swatch.setAttribute('aria-hidden','true');
        setTokens(swatch,palette,FONT_PAIRINGS[0]);
        const side=node('span','palette-mini-sidebar'),page=node('span','palette-mini-page');
        page.append(node('span','palette-mini-heading'),node('span','palette-mini-line'),node('span','palette-mini-line short'),node('span','palette-mini-button'));
        swatch.append(side,page);
        const caption=node('span','palette-caption');caption.append(node('span','',palette.name));
        const mark=node('span','palette-selected');mark.append(icon('check'));caption.append(mark);
        label.append(input,swatch,caption);choices.append(label);
      }
      group.append(choices);this.options.append(group);
    }
    const fontGroup=node('fieldset','font-group');fontGroup.append(node('legend','appearance-label','字体搭配'));
    const fontGrid=node('div','font-pairing-grid');
    for(const pairing of FONT_PAIRINGS) {
      const label=node('label','font-choice'),input=node('input');input.type='radio';input.name='font_pairing';input.value=pairing.id;
      input.addEventListener('change',()=>{if(input.checked){this.values.font_pairing=pairing.id;this.update();this.onChange();}});
      const specimen=node('span','font-choice-specimen','一餐一味');specimen.style.fontFamily=pairing.title;specimen.style.fontWeight=pairing.titleWeight;
      const copy=node('span','font-choice-copy');copy.append(node('span','font-choice-name',pairing.name),node('small','',pairing.note));
      label.append(input,specimen,copy);fontGrid.append(label);
    }
    fontGroup.append(fontGrid);this.options.append(fontGroup);
    const previewArea=this.previewArea=node('details','appearance-preview-area');previewArea.open=this.wide?.matches===true;previewArea.setAttribute('aria-label','所选配色与字体的效果预览');
    const heading=node('summary','appearance-preview-heading');heading.append(node('span','','效果预览'));
    this.previewName=node('span');this.previewName.setAttribute('aria-live','polite');heading.append(this.previewName,icon('chevron-down'));
    this.preview=node('div','appearance-preview');
    const bar=node('div','appearance-preview-bar');bar.append(icon('cooking-pot'),node('span','','ChefMate'));
    const body=node('div','appearance-preview-body');
    body.append(node('h3','appearance-preview-title','把每一餐，照顾好。'),node('span','appearance-preview-caption','今日饮食 · 示例数据'));
    const metrics=node('dl','appearance-preview-metrics');
    for(const [name,value,unit]of [['热量','385.2','kcal'],['蛋白质','26.2','g']]) {
      const item=node('div'),number=node('dd','',value);number.append(node('small','',unit));item.append(node('dt','',name),number);metrics.append(item);
    }
    const chat=node('div','appearance-preview-chat');chat.append(node('strong','','小锅搭子'),node('p','','辛苦啦，炒滑蛋已经做好，也帮你记下营养了。口感还合胃口吗？'));
    const action=node('span','appearance-preview-action','看看最近的营养');
    body.append(metrics,chat,action);this.preview.append(bar,body);
    previewArea.append(heading,this.preview,node('p','appearance-preview-note','选择后预览，保存后应用到整个工作台。'));
    this.host.append(this.options,previewArea);
  }
  update() {
    const resolved=resolveAppearance(this.values,this.media?.matches || false);
    const mode=this.values.theme==='system'?(this.previewMode || resolved.theme):resolved.theme;
    const palette=PALETTES.find(item=>item.id===this.values[`${mode}_palette`]) || resolved.palette;
    for(const group of this.options.querySelectorAll('[data-palette-mode]'))group.hidden=this.values.theme!=='system' && group.dataset.paletteMode!==resolved.theme;
    for(const input of this.options.querySelectorAll('input')) {
      input.checked=input.value===this.values[input.name];
      input.closest('label').classList.toggle('selected',input.checked);
    }
    setTokens(this.preview,palette,resolved.fonts);
    this.preview.dataset.palette=palette.id;this.preview.dataset.fontPairing=resolved.fonts.id;
    this.previewName.textContent=`${palette.name} · ${resolved.fonts.name}`;
  }
  dispose(){this.media?.removeEventListener('change',this.onSystemChange);this.wide?.removeEventListener('change',this.onWidthChange);}
}
