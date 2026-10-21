import {icon} from './icons.js';

export const STARTERS=[
  {id:'craving',icon:'utensils',prefix:'今天想吃',blank:'虾',hint:'按你想吃的，挑几道好菜',text:value=>`我想吃${value}，推荐几道适合家常做的菜，先让我选一选。`},
  {id:'fridge',icon:'utensils',prefix:'冰箱里有',blank:'鸡蛋、西红柿',hint:'用现有食材，少买一点',text:value=>`我家有${value}，请尽量利用现有食材，帮我搭配一餐。`},
  {id:'quick',icon:'timer',prefix:'留给做饭的时间是',blank:'20 分钟',hint:'把时间也放进菜单里',text:value=>`我只有${value}做饭，一个人吃，请推荐适合的简单菜谱。`},
  {id:'people',icon:'layers',prefix:'今晚一起吃饭的有',blank:'3 人',hint:'份量、搭配，一起安排',text:value=>`今晚${value}吃饭，帮我搭配一份有荤有素的菜单，并说明大致份量。`},
  {id:'nutrition',icon:'sun-moon',title:'想吃得均衡一点',hint:'看看每份营养和估算依据',text:()=> '帮我选几道高蛋白、蔬菜丰富的家常菜，说明每份营养和数据是否估算。'},
  {id:'shopping',icon:'shopping-bag',title:'把采购也安排好',hint:'选菜单，再列一份采购清单',text:()=> '帮我安排两个人的晚餐，先让我选菜，确认后把食材合并成采购清单。'},
  {id:'beginner',icon:'cooking-pot',title:'第一次做饭也没关系',hint:'从新手菜，到备菜和步骤',text:()=> '我是做饭新手，只有炒锅和炉灶。帮我选一道简单的菜，再一步步带我备菜和烹饪。'},
  {id:'preferences',icon:'list-filter',prefix:'这顿饭不吃',blank:'香菜和辣椒',hint:'先记住口味，再推荐',text:value=>`这顿饭不吃${value}，帮我整理用餐偏好，再推荐几道合适的菜。`},
  {id:'image',icon:'image-plus',title:'看看冰箱里的食材',hint:'附一张图片，一起想想吃什么',images:true,text:()=> '请看看我附上的食材图片，告诉我有哪些适合的菜；拿不准的食材先向我确认。'},
  {id:'cooking-photo',icon:'image-plus',title:'这一步做对了吗',hint:'拍下锅里的状态，一起判断',images:true,text:()=> '我会附上一张烹饪中的图片，请帮我看看当前状态，接下来该怎么做？'},
];

export function drawStarters(supportsImages,random=Math.random) {
  const pool=STARTERS.filter(item=>!item.images || supportsImages);
  for(let i=pool.length-1;i>0;i--){const j=Math.floor(random()*(i+1));[pool[i],pool[j]]=[pool[j],pool[i]];}
  return pool.slice(0,3);
}

export function renderStarters(container,{supportsImages,onUse,random}={}) {
  container.replaceChildren();
  for(const template of drawStarters(supportsImages,random)) {
    const card=document.createElement('article');card.className='starter-card';card.dataset.template=template.id;
    const mark=document.createElement('span');mark.className='starter-icon';mark.append(icon(template.icon));
    const title=document.createElement('div');title.className='starter-title';
    let field;
    if(template.blank){
      title.append(document.createTextNode(`${template.prefix} `));
      field=document.createElement('input');field.className='starter-blank';field.value=template.blank;field.maxLength=90;
      field.setAttribute('aria-label',template.prefix);field.style.setProperty('--blank-length',Math.min(template.blank.length+1,10));
      field.addEventListener('focus',()=>field.select());title.append(field);
    } else title.textContent=template.title;
    const hint=document.createElement('p');hint.textContent=template.hint;
    const use=document.createElement('button');use.type='button';use.className='starter-use';use.setAttribute('aria-label',`使用模板：${template.title || template.prefix}`);
    use.append(document.createTextNode('试试这个'),icon('arrow-up-right'));
    use.addEventListener('click',()=>{
      const value=field?.value ?? '';
      onUse?.(template.text(value),field ? value : null,template.images);
    });
    field?.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();use.click();}});
    card.append(mark,title,hint,use);container.append(card);
  }
}
