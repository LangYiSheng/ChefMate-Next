import { useMemo, useState, type CSSProperties } from 'react';
import { Brand, Icon } from './Icon';

const starters = [
  { category: 'kitchen', icon: 'utensils', title: '今天想吃', blank: '虾', hint: '按你想吃的，挑几道好菜', text: (v: string) => `我想吃${v}，推荐几道适合家常做的菜，先让我选一选。` },
  { category: 'kitchen', icon: 'utensils', title: '冰箱里有', blank: '鸡蛋、西红柿', hint: '用现有食材，少买一点', text: (v: string) => `我家有${v}，请尽量利用现有食材，帮我搭配一餐。` },
  { category: 'kitchen', icon: 'timer', title: '留给做饭的时间是', blank: '20 分钟', hint: '把时间也放进菜单里', text: (v: string) => `我只有${v}做饭，一个人吃，请推荐适合的简单菜谱。` },
  { category: 'kitchen', icon: 'layers', title: '今晚一起吃饭的有', blank: '3 人', hint: '份量、搭配，一起安排', text: (v: string) => `今晚${v}吃饭，帮我搭配一份有荤有素的菜单，并说明大致份量。` },
  { category: 'kitchen', icon: 'sun-moon', title: '想吃得均衡一点', hint: '看看每份营养和估算依据', text: () => '帮我选几道高蛋白、蔬菜丰富的家常菜，说明每份营养和数据是否估算。' },
  { category: 'kitchen', icon: 'list-checks', title: '把准备工作安排好', hint: '选菜单，再逐项确认备料', text: () => '帮我安排两个人的晚餐，先让我选菜，确认后整理备菜清单，我会告诉你哪些已经有了。' },
  { category: 'kitchen', icon: 'cooking-pot', title: '第一次做饭也没关系', hint: '从新手菜，到备菜和步骤', text: () => '我是做饭新手，只有炒锅和炉灶。帮我选一道简单的菜，再一步步带我备菜和烹饪。' },
  { category: 'kitchen', icon: 'list-filter', title: '这顿饭不吃', blank: '香菜和辣椒', hint: '先记住口味，再推荐', text: (v: string) => `这顿饭不吃${v}，帮我整理用餐偏好，再推荐几道适合的菜。` },
  { category: 'kitchen', icon: 'image-plus', title: '看看冰箱里的食材', image: true, hint: '附一张图片，一起想想吃什么', text: () => '请看看我附上的食材图片，告诉我有哪些适合的菜；拿不准的食材先向我确认。' },
  { category: 'kitchen', icon: 'camera', title: '这一步做对了吗', image: true, hint: '拍下锅里的状态，一起判断', text: () => '我会附上一张烹饪中的图片，请帮我看看当前状态，接下来该怎么做？' },
  { category: 'recipes', icon: 'book-open', title: '留住家里的拿手菜', hint: '粘贴做法，整理成个人菜谱草稿', text: () => '我想把家里的拿手菜整理成个人菜谱。我接下来会发做法，信息不全的地方请问我，先生成草稿让我核对。' },
  { category: 'recipes', icon: 'images', title: '收藏这张菜谱截图', image: true, hint: '从图片整理，核对后再保存', text: () => '我会附上一张菜谱截图，请整理成个人菜谱草稿，缺少的人数、时间和克重保留未知，等我核对后再保存。' },
  { category: 'recipes', icon: 'search', title: '再做一次我的', blank: '番茄炒蛋', hint: '找回个人菜谱，接着备菜做饭', text: (v: string) => `请帮我搜索个人菜谱里的「${v}」，先让我看看实际做法，再安排这顿饭。` },
  { category: 'journal', icon: 'utensils', title: '刚刚吃了', blank: '一碗米饭和一份炒青菜', hint: '先整理饮食草稿，确认后记入', text: (v: string) => `我刚刚吃了${v}。帮我整理饮食记录草稿，份量不清楚的先问我，估算要标明，等我确认后再保存。` },
  { category: 'journal', icon: 'camera', title: '拍照记下这一餐', image: true, hint: '看图核对食物、份量和营养', text: () => '我想记录照片里的这一餐。请先识别食物并向我确认份量，再整理饮食草稿，估算和未知值分别保留，等我确认。' },
  { category: 'journal', icon: 'layers', title: '这一周吃得怎么样', hint: '回顾真实记录，看看营养趋势', text: () => '请根据我最近七天实际保存的饮食记录，回顾营养和饮食习惯；没有记录的日子不要猜测，给我几条适合下一餐的小建议。' },
  { category: 'memory', icon: 'list-filter', title: '以后做饭请记住', blank: '喜欢清淡，少放辣椒', hint: '让口味偏好陪着每一次推荐', text: (v: string) => `我的做饭偏好是：${v}。请帮我维护长期记忆，先核对要记住的内容。` },
  { category: 'memory', icon: 'message-circle', title: '找回上次做饭的灵感', hint: '查找真实历史，再聊聊这次怎么做', text: () => '帮我找找最近聊过或做过的菜，结合实际历史说说哪一道适合再做一次。没有相关记录就告诉我。' },
];

function Starter({ item, onUse }: { item: typeof starters[number]; onUse: (text: string, image?: boolean) => void }) {
  const [value, setValue] = useState(item.blank || '');
  return <article className="starter-card"><span className="starter-icon"><Icon name={item.icon} /></span>
    <div className="starter-title">{item.title}{item.blank && <> <input className="starter-blank" value={value} maxLength={90} aria-label={item.title}
      style={{ '--blank-width': `${Math.max(3, Math.min(18, value.length + 1))}em` } as CSSProperties} onFocus={event => event.currentTarget.select()} onChange={event => setValue(event.target.value)}
      onKeyDown={event => { if (event.key === 'Enter') onUse(item.text(value), item.image); }} /></>}</div>
    <p>{item.hint}</p><button type="button" className="starter-use" onClick={() => onUse(item.text(value), item.image)}>试试这个<Icon name="arrow-up-right" /></button>
  </article>;
}

export function Welcome({ supportsImages, onUse }: { supportsImages: boolean; onUse: (text: string, image?: boolean) => void }) {
  const [category, setCategory] = useState('all');
  const [draw, setDraw] = useState(0);
  const visible = useMemo(() => starters.filter(item => (!item.image || supportsImages) && (category === 'all' || item.category === category))
    .map(item => ({ item, sort: Math.random() })).sort((a, b) => a.sort - b.sort).slice(0, 3).map(({ item }) => item), [category, draw, supportsImages]);
  return <section className="welcome"><Brand className="welcome-icon" /><h1>今天想吃什么？</h1>
    <p>说说食材、时间和人数，<br className="welcome-break" />一起选菜、备菜，把这顿饭做好。</p>
    <div className="starter-categories" aria-label="按用途查看开场示例">{[['all', '随便看看'], ['kitchen', '选菜做饭'], ['recipes', '个人菜谱'], ['journal', '饮食记录'], ['memory', '了解你']].map(([id, label]) =>
      <button key={id} type="button" aria-pressed={category === id} onClick={() => setCategory(id)}>{label}</button>)}</div>
    <div className="starter-templates">{visible.map(item => <Starter key={item.title} item={item} onUse={onUse} />)}</div>
    <button type="button" className="starter-shuffle" onClick={() => setDraw(draw + 1)}><Icon name="refresh-cw" />换一组灵感</button>
  </section>;
}
