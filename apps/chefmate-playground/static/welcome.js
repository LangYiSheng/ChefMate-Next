import {icon} from './icons.js';

export const STARTERS=[
  {id:'craving',icon:'utensils',prefix:'今天想吃',blank:'虾',hint:'按你想吃的，挑几道好菜',text:value=>`我想吃${value}，推荐几道适合家常做的菜，先让我选一选。`},
  {id:'fridge',icon:'utensils',prefix:'冰箱里有',blank:'鸡蛋、西红柿',hint:'用现有食材，少买一点',text:value=>`我家有${value}，请尽量利用现有食材，帮我搭配一餐。`},
  {id:'quick',icon:'timer',prefix:'留给做饭的时间是',blank:'20 分钟',hint:'把时间也放进菜单里',text:value=>`我只有${value}做饭，一个人吃，请推荐适合的简单菜谱。`},
  {id:'people',icon:'layers',prefix:'今晚一起吃饭的有',blank:'3 人',hint:'份量、搭配，一起安排',text:value=>`今晚${value}吃饭，帮我搭配一份有荤有素的菜单，并说明大致份量。`},
  {id:'nutrition',icon:'sun-moon',title:'想吃得均衡一点',hint:'看看每份营养和估算依据',text:()=> '帮我选几道高蛋白、蔬菜丰富的家常菜，说明每份营养和数据是否估算。'},
  {id:'preparation',icon:'list-checks',title:'把准备工作安排好',hint:'选菜单，再逐项确认备料',text:()=> '帮我安排两个人的晚餐，先让我选菜，确认后整理备菜清单，我会告诉你哪些已经有了。'},
  {id:'beginner',icon:'cooking-pot',title:'第一次做饭也没关系',hint:'从新手菜，到备菜和步骤',text:()=> '我是做饭新手，只有炒锅和炉灶。帮我选一道简单的菜，再一步步带我备菜和烹饪。'},
  {id:'preferences',icon:'list-filter',prefix:'这顿饭不吃',blank:'香菜和辣椒',hint:'先记住口味，再推荐',text:value=>`这顿饭不吃${value}，帮我整理用餐偏好，再推荐几道合适的菜。`},
  {id:'image',icon:'image-plus',title:'看看冰箱里的食材',hint:'附一张图片，一起想想吃什么',images:true,text:()=> '请看看我附上的食材图片，告诉我有哪些适合的菜；拿不准的食材先向我确认。'},
  {id:'cooking-photo',icon:'image-plus',title:'这一步做对了吗',hint:'拍下锅里的状态，一起判断',images:true,text:()=> '我会附上一张烹饪中的图片，请帮我看看当前状态，接下来该怎么做？'},
  {id:'recipe-text',category:'recipes',icon:'book-open',title:'留住家里的拿手菜',hint:'粘贴做法，整理成个人菜谱草稿',text:()=> '我想把家里的拿手菜整理成个人菜谱。我接下来会发做法，信息不全的地方请问我，先生成草稿让我核对。'},
  {id:'recipe-image',category:'recipes',icon:'images',title:'收藏这张菜谱截图',hint:'从图片整理，核对后再保存',images:true,text:()=> '我会附上一张菜谱截图，请整理成个人菜谱草稿，缺少的人数、时间和克重保留未知，等我核对后再保存。'},
  {id:'recipe-reuse',category:'recipes',icon:'search',prefix:'再做一次我的',blank:'番茄炒蛋',hint:'找回个人菜谱，接着备菜做饭',text:value=>`请帮我搜索个人菜谱里的「${value}」，先让我看看实际做法，再安排这顿饭。`},
  {id:'journal-text',category:'journal',icon:'utensils',prefix:'刚刚吃了',blank:'一碗米饭和一份炒青菜',hint:'先整理饮食草稿，确认后记入',text:value=>`我刚刚吃了${value}。帮我整理饮食记录草稿，份量不清楚的先问我，估算要标明，等我确认后再保存。`},
  {id:'journal-image',category:'journal',icon:'camera',title:'拍照记下这一餐',hint:'看图核对食物、份量和营养',images:true,text:()=> '我想记录照片里的这一餐。请先识别食物并向我确认份量，再整理饮食草稿，估算和未知值分别保留，等我确认。'},
  {id:'journal-review',category:'journal',icon:'layers',title:'这一周吃得怎么样',hint:'回顾真实记录，看看营养趋势',text:()=> '请根据我最近七天实际保存的饮食记录，回顾营养和饮食习惯；没有记录的日子不要猜测，给我几条适合下一餐的小建议。'},
  {id:'memory',category:'memory',icon:'list-filter',prefix:'以后做饭请记住',blank:'喜欢清淡，少放辣椒',hint:'让口味偏好陪着每一次推荐',text:value=>`我的做饭偏好是：${value}。请帮我维护长期记忆，先核对要记住的内容。`},
  {id:'history',category:'memory',icon:'message-circle',title:'找回上次做饭的灵感',hint:'查找真实历史，再聊聊这次怎么做',text:()=> '帮我找找最近聊过或做过的菜，结合实际历史说说哪一道适合再做一次。没有相关记录就告诉我。'},
];

export function drawStarters(supportsImages,random=Math.random,category='all') {
  const pool=STARTERS.filter(item=>(!item.images || supportsImages) && (category==='all' || (item.category || 'kitchen')===category));
  for(let i=pool.length-1;i>0;i--){const j=Math.floor(random()*(i+1));[pool[i],pool[j]]=[pool[j],pool[i]];}
  return pool.slice(0,3);
}

export function renderStarters(container,{supportsImages,onUse,random,category=container.dataset.category || 'all'}={}) {
  container.dataset.category=category;
  let filters=container.previousElementSibling;
  if(!filters?.classList.contains('starter-categories')){filters=document.createElement('div');filters.className='starter-categories';filters.setAttribute('aria-label','按用途查看开场示例');container.before(filters);}
  filters.replaceChildren();
  for(const [key,label]of [['all','随便看看'],['kitchen','选菜做饭'],['recipes','个人菜谱'],['journal','饮食记录'],['memory','了解你']]){
    const filter=document.createElement('button');filter.type='button';filter.textContent=label;filter.setAttribute('aria-pressed',String(category===key));
    filter.onclick=()=>{renderStarters(container,{supportsImages,onUse,random,category:key});filters.querySelector(`[aria-pressed="true"]`)?.focus();};filters.append(filter);
  }
  container.replaceChildren();
  for(const template of drawStarters(supportsImages,random,category)) {
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
