import {useCallback, useEffect, useRef, useState, type CSSProperties} from 'react';
import {api} from '../../runtime/http';
import {Icon} from '../../components/Icon';
import {CenterButton, CenterTabs} from './CenterControls';
import {FoodLogEditor} from './FoodLogEditor';
import {JournalEditor, JournalView} from './JournalView';
import {RecipeEditor} from './RecipeEditor';
import type {Data} from './center-model';
import './centers-react.css';

export type KitchenCentersProps = {
  page: 'recipes' | 'journal'; onRecipe?: (id: string) => void; notify?: (text: string) => void;
  onChat?: (text: string) => void; onRecord?: () => void; onReturn?: () => void;
  onDirtyChange?: (dirty: boolean, busy: boolean) => void;
};
const recipeSources = [['all', '全部菜谱'], ['user', '我的菜谱'], ['favorites', '已收藏'], ['drafts', '待核对'], ['deleted', '回收站']] as const;
const journalTabs = [['overview', '营养概览'], ['records', '饮食记录'], ['drafts', '待核对'], ['production', '做饭记录'], ['deleted', '回收站']] as const;
type Selection = {kind: 'recipe' | 'food' | 'journal'; id: string; title: string; record?: Data; loading?: boolean; error?: string};
export function KitchenCenters({page, onRecipe, notify, onChat, onRecord, onReturn, onDirtyChange}: KitchenCentersProps) {
  const [source, setSource] = useState('all'), [search, setSearch] = useState(''), [query, setQuery] = useState(''), [offset, setOffset] = useState(0), [days, setDays] = useState(7), [journalTab, setJournalTab] = useState('overview');
  const [data, setData] = useState<Data | null>(null), [loading, setLoading] = useState(true), [listError, setListError] = useState(''), [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null), [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [mutationBusy, setMutationBusy] = useState(false), [notice, setNotice] = useState(''), [isError, setIsError] = useState(false);
  const [confirmation, setConfirmation] = useState<null | {title: string; text: string; accept: string; action: () => void}>(null), [inspectorWidth, setInspectorWidth] = useState(500), [compact, setCompact] = useState(false);
  const detailAbort = useRef<AbortController | null>(null), detailSequence = useRef(0), alive = useRef(true), working = useRef(false), allowedNavigation = useRef(false), previousFocus = useRef<HTMLElement | null>(null), inspector = useRef<HTMLElement>(null), host = useRef<HTMLElement>(null), stopResize = useRef<(() => void) | null>(null);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const say = useCallback((text: string, failed = false) => {setNotice(text); setIsError(failed);}, []);
  const adoptSelectedRecord = useCallback((record: Data) => setSelection(previous => previous && previous.id === (record.recipe_id || record.id) ? {...previous, record, title: previous.kind === 'recipe' ? record.document.name : record.document.title} : previous), []);
  useEffect(() => {alive.current = true; return () => {alive.current = false; detailAbort.current?.abort(); stopResize.current?.();};}, []);
  useEffect(() => {
    const update = () => {const width = host.current?.getBoundingClientRect().width || 0; setCompact(width > 0 ? width < 900 : window.matchMedia('(max-width:900px)').matches);};
    update(); const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null; if (host.current) observer?.observe(host.current);
    window.addEventListener('resize', update); return () => {observer?.disconnect(); window.removeEventListener('resize', update);};
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setListError('');
    const endpoint = page === 'recipes' ? `/api/cookbook?source=${source}&query=${encodeURIComponent(query)}&offset=${offset}&limit=12` : `/api/journal?days=${days}`;
    api(endpoint, {signal: controller.signal}).then(result => {if (!controller.signal.aborted) setData(result);}).catch(error => {if (!controller.signal.aborted) setListError(error.message);}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [page, source, query, offset, days, revision]);
  const close = useCallback(() => {detailAbort.current?.abort(); detailSequence.current++; setSelection(null); setDirty(false); setBusy(false); requestAnimationFrame(() => previousFocus.current?.isConnected && previousFocus.current.focus({preventScroll: true}));}, []);
  const guard = (action: () => void) => {
    if (busy || mutationBusy) {say('正在保存，请稍等完成后再离开。', true); return;}
    if (dirty) setConfirmation({title: '放弃尚未保存的修改？', text: '当前草稿仍保留在服务器，离开会丢失这次尚未保存的修改。', accept: '放弃修改', action: () => {allowedNavigation.current = true; action(); queueMicrotask(() => {allowedNavigation.current = false;});}});
    else action();
  };
  useEffect(() => {onDirtyChange?.(dirty, busy || mutationBusy);}, [dirty, busy, mutationBusy, onDirtyChange]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {if (dirty || busy || mutationBusy) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', unload); return () => window.removeEventListener('beforeunload', unload);
  }, [dirty, busy, mutationBusy]);
  useEffect(() => {
    const navigate = (event: Event) => {
      const navigation = event as CustomEvent<{page: string; resume?: () => void}>;
      if (allowedNavigation.current || navigation.detail?.page === page || !dirty && !busy && !mutationBusy) return;
      event.preventDefault();
      if (busy || mutationBusy) say('正在保存，请稍等完成后再离开。', true);
      else if (navigation.detail?.resume) setConfirmation({title: '放弃尚未保存的修改？', text: '离开会丢失这次尚未保存的修改。', accept: '放弃修改', action: navigation.detail.resume});
    };
    window.addEventListener('chefmate:before-page-change', navigate);
    return () => window.removeEventListener('chefmate:before-page-change', navigate);
  }, [page, dirty, busy, mutationBusy, say]);
  useEffect(() => {close(); setData(null); setNotice('');}, [page, close]);
  useEffect(() => {if (selection) inspector.current?.querySelector<HTMLButtonElement>('[data-close-inspector]')?.focus({preventScroll: true});}, [selection?.id]);
  const open = (kind: Selection['kind'], item: Data) => guard(async () => {
    detailAbort.current?.abort(); const controller = new AbortController(); detailAbort.current = controller; const epoch = ++detailSequence.current;
    const id = kind === 'recipe' ? item.recipe_id : item.id, title = kind === 'recipe' ? item.name : kind === 'food' ? item.document.title : '这顿饭';
    previousFocus.current = document.activeElement as HTMLElement; setDirty(false); setBusy(false); say(''); setSelection({kind, id, title, loading: kind !== 'journal', record: kind === 'journal' ? item : undefined});
    if (kind === 'journal') return;
    try {
      const path = kind === 'food' ? `/api/food-logs/${encodeURIComponent(id)}` : item.origin === 'user' ? `/api/cookbook/${encodeURIComponent(id)}` : `/api/recipes/${encodeURIComponent(id)}`;
      const result = await api(path, {signal: controller.signal});
      if (epoch !== detailSequence.current || !alive.current) return;
      const record = kind === 'recipe' && item.origin !== 'user' ? {...result.recipe, document: result.recipe, status: 'saved'} : result;
      setSelection({kind, id, title, record});
    } catch (error) {if ((error as Error).name !== 'AbortError' && epoch === detailSequence.current) setSelection({kind, id, title, error: (error as Error).message});}
  });
  const mutate = async (operation: () => Promise<any>, success: string, closeAfter = false) => {
    if (working.current) return;
    working.current = true; setMutationBusy(true);
    try {await operation(); if (!alive.current) return; if (closeAfter) close(); say(success); notify?.(success); refresh();}
    catch (error) {if (alive.current) say((error as Error).message, true);}
    finally {working.current = false; if (alive.current) setMutationBusy(false);}
  };
  const remove = (record: Data, kind: 'recipe' | 'food') => {
    if (busy || mutationBusy) return;
    const title = kind === 'recipe' ? record.document.name : record.document.title, id = kind === 'recipe' ? record.recipe_id : record.id;
    setConfirmation({title: `把「${title}」移到回收站？`, text: `${dirty ? '未保存的修改会丢失。' : ''}${kind === 'food' ? '移除后不计入个人营养统计，之后可以恢复。' : '之后可在菜谱中心恢复，做过的饭和正在进行的烹饪记录会保留。'}`, accept: '移到回收站', action: () => {void mutate(() => api(`/api/${kind === 'recipe' ? 'cookbook' : 'food-logs'}/${encodeURIComponent(id)}?revision=${record.revision}`, {method: 'DELETE'}), '已移到回收站，可以随时恢复。', true);}});
  };
  const restore = (record: Data, kind: 'recipe' | 'food') => mutate(() => api(`/api/${kind === 'recipe' ? 'cookbook' : 'food-logs'}/${encodeURIComponent(kind === 'recipe' ? record.recipe_id : record.id)}/restore`, {method: 'POST', body: JSON.stringify({revision: record.revision})}), kind === 'recipe' && record.deleted_from === 'draft' ? '草稿已恢复到“待核对”。' : '记录已恢复。');
  const saved = (published: boolean) => {if (published) {close(); if (page === 'recipes') {setSource('user'); setOffset(0);} else setJournalTab('overview');} refresh();};
  const chat = (text: string) => guard(() => {close(); if (onChat) onChat(text); else notify?.('请返回对话，告诉小锅搭子你的想法。');});
  const beginResize = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault(); stopResize.current?.(); const start = event.clientX, width = inspectorWidth;
    const move = (pointer: PointerEvent) => setInspectorWidth(Math.max(360, Math.min(800, (host.current?.clientWidth || 1000) - 310, width + start - pointer.clientX)));
    const stop = () => {window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); window.removeEventListener('pointercancel', stop); stopResize.current = null;};
    stopResize.current = stop;
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop, {once: true}); window.addEventListener('pointercancel', stop, {once: true});
  };
  return <section className="center-host react-centers" aria-label="个人厨房" ref={host}><div className={`center-layout ${selection ? 'has-inspector' : ''} ${compact ? 'compact-inspector' : ''}`} style={{'--inspector-width': `${inspectorWidth}px`} as CSSProperties}>
    <div className="center-main-pane"><header className="center-heading"><div className="center-title"><h1>{page === 'recipes' ? '菜谱中心' : '食光手记'}</h1><p className="center-muted">{page === 'recipes' ? '收好喜欢的味道，随时开始做。' : '记下每一顿饭，慢慢找到适合自己的味道。'}</p></div><div className="center-heading-actions">{onReturn && <CenterButton onClick={() => guard(onReturn)}>返回对话</CenterButton>}{page === 'recipes' ? <CenterButton primary onClick={() => chat('我想制作自己的菜谱。我会提供截图或文字，请帮我整理成可核对的菜谱草稿。')}>新建菜谱</CenterButton> : <CenterButton primary onClick={() => guard(() => {close(); if (onRecord) onRecord(); else onChat?.('我想记录一餐，接下来会告诉你吃了什么。');})}>记一餐</CenterButton>}</div></header>
      <div className="center-navigation">{page === 'recipes' ? <><CenterTabs items={recipeSources} selected={source} onSelect={key => guard(() => {close(); setSource(key); setOffset(0);})} label="菜谱分类"/><form className="center-searchbar" onSubmit={event => {event.preventDefault(); guard(() => {close(); setQuery(search.trim()); setOffset(0);});}}><input className="center-input" type="search" aria-label="搜索菜谱" placeholder="搜索菜名或我的菜谱食材" value={search} maxLength={200} onChange={event => setSearch(event.target.value)}/><button type="submit" className="center-button">搜索</button></form></> : <><CenterTabs items={journalTabs} selected={journalTab} onSelect={key => guard(() => {close(); setJournalTab(key);})} label="食光手记内容"/><div className="center-journal-toolbar"><select className="center-input" aria-label="记录时间范围" value={days} onChange={event => {const selectedDays = Number(event.target.value); guard(() => {close(); setDays(selectedDays);});}}>{[7, 30, 90].map(day => <option key={day} value={day}>最近 {day} 天</option>)}</select><CenterButton aria-label="刷新记录" onClick={refresh}><Icon name="refresh-cw"/></CenterButton></div></>}</div>
      {notice && <p role="status" className={`center-notice ${isError ? 'center-error' : ''}`}>{notice}</p>}
      <div className="center-content" aria-busy={loading}>{listError ? <div className="center-empty"><p className="center-error" role="alert">{listError}</p><CenterButton onClick={refresh}>重新读取</CenterButton></div> : !data ? <p className="center-empty">正在读取…</p> : page === 'journal' ? <JournalView data={data} tab={journalTab} onTab={setJournalTab} onFood={record => open('food', record)} onRestore={record => {void restore(record, 'food');}} onJournal={entry => open('journal', entry)} busy={mutationBusy}/> : <><p className="center-result-count">共 {data.total} {source === 'drafts' ? '份草稿' : '道菜谱'}{source === 'deleted' ? ' · 可随时恢复' : ''}</p><div className="center-recipe-list">{data.items.map((item: Data) => <article className={`center-recipe-row ${selection?.id === item.recipe_id ? 'is-selected' : ''}`} key={item.recipe_id}><div className="center-recipe-identity"><span className="center-eyebrow">{item.status === 'deleted' ? '已移入回收站' : item.status === 'draft' ? '待核对' : item.origin === 'user' ? '我的菜谱' : '精选菜谱'}</span>{item.status === 'deleted' ? <h2 className="center-recipe-title">{item.name}</h2> : <button type="button" className="center-button center-recipe-title" aria-label={`${item.status === 'draft' ? '核对' : '查看'}${item.name}`} onClick={() => open('recipe', item)}>{item.name}</button>}<p className="center-muted">{Number.isFinite(item.total_time_min) ? `${item.total_time_min} 分钟` : '时间待确认'} · {Number.isFinite(item.servings) ? `原配方 ${item.servings} 份` : '原份量未知'}</p>{item.description && <p className="center-recipe-description">{item.description.slice(0, 100)}</p>}</div><div className="center-row-actions">{item.status === 'deleted' ? <CenterButton disabled={mutationBusy} onClick={() => restore(item, 'recipe')}>恢复菜谱</CenterButton> : <><CenterButton onClick={() => open('recipe', item)}>{item.status === 'draft' ? '核对草稿' : '查看做法'}</CenterButton>{item.status !== 'draft' && <CenterButton aria-label={item.favorite ? '取消收藏' : '收藏菜谱'} aria-pressed={item.favorite} disabled={mutationBusy} onClick={() => mutate(() => api(`/api/favorites/${encodeURIComponent(item.recipe_id)}`, {method: 'PUT', body: JSON.stringify({favorite: !item.favorite})}), item.favorite ? '已取消收藏。' : '已收藏菜谱。')}><Icon name="star"/></CenterButton>}</>}</div></article>)}{!data.items.length && <div className="center-empty"><h2>{source === 'deleted' ? '回收站是空的' : source === 'drafts' ? '把想做的菜交给小锅搭子' : '还没有找到菜谱'}</h2><p className="center-muted">{source === 'drafts' ? '发一张截图，或粘贴一段做法。整理后的草稿会放在这里，核对并保存后就能开始做。' : source === 'deleted' ? '移除的个人菜谱会保留在这里，之后还可以恢复。' : '换个关键词，或让助手帮你整理喜欢的做法。'}</p></div>}</div><nav className="center-pager" aria-label="菜谱翻页"><CenterButton disabled={!offset || loading} onClick={() => setOffset(value => Math.max(0, value - 12))}>上一页</CenterButton><span className="center-muted">{Math.floor(offset / 12) + 1} / {Math.max(1, Math.ceil(data.total / 12))}</span><CenterButton disabled={offset + 12 >= data.total || loading} onClick={() => setOffset(value => value + 12)}>下一页</CenterButton></nav></>}</div>
    </div>
    {selection && <aside className="center-inspector" role={compact ? 'dialog' : undefined} aria-modal={compact ? true : undefined} aria-label={selection.title} ref={inspector} onKeyDown={event => {
      if (event.key === 'Escape') {event.preventDefault(); guard(close);}
      if (event.key !== 'Tab' || !compact) return;
      const targets = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')).filter(target => target.tabIndex >= 0 && target.getClientRects().length);
      const first = targets[0], last = targets.at(-1); if (!first) return;
      if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {event.preventDefault(); (event.shiftKey ? last : first)?.focus();}
    }}><div className="center-resize-handle" role="separator" aria-label="调整详情宽度" aria-orientation="vertical" aria-valuenow={inspectorWidth} tabIndex={0} onPointerDown={beginResize} onKeyDown={event => {if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {event.preventDefault(); setInspectorWidth(width => Math.max(360, Math.min(800, width + (event.key === 'ArrowLeft' ? 20 : -20))));}}}/><header className="center-inspector-header"><div><span className="center-eyebrow">{selection.kind === 'recipe' ? selection.record?.status === 'draft' ? '待核对草稿' : '菜谱详情' : selection.kind === 'food' ? selection.record?.status === 'draft' ? '待核对饮食' : '个人饮食记录' : '做饭记录'}</span><h2>{selection.title}</h2></div><CenterButton data-close-inspector aria-label="关闭详情" onClick={() => guard(close)}><Icon name="x"/></CenterButton></header><div className="center-inspector-body">{selection.loading ? <p>正在读取…</p> : selection.error ? <p role="alert" className="center-error">{selection.error}</p> : selection.record && (selection.kind === 'recipe' ? <RecipeEditor key={selection.id} initial={selection.record} onRecord={adoptSelectedRecord} onDirty={setDirty} onBusy={setBusy} onStatus={say} onSaved={saved} onDelete={record => remove(record, 'recipe')} onRecipe={onRecipe ? id => guard(() => {close(); onRecipe(id);}) : undefined} onChat={chat}/> : selection.kind === 'food' ? <FoodLogEditor key={selection.id} initial={selection.record} onRecord={adoptSelectedRecord} onDirty={setDirty} onBusy={setBusy} onStatus={say} onSaved={saved} onDelete={record => remove(record, 'food')}/> : <JournalEditor key={selection.id} entry={selection.record} onDirty={setDirty} onBusy={setBusy} onStatus={say} onSaved={saved} onChat={chat}/>)}</div>{notice && <p className={`center-inspector-status ${isError ? 'center-error' : ''}`} role="status">{notice}</p>}</aside>}
    {confirmation && <div className="react-confirm-backdrop" onKeyDown={event => {if (event.key === 'Escape') setConfirmation(null); if (event.key === 'Tab') {const targets = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')); const first = targets[0], last = targets.at(-1); if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {event.preventDefault(); (event.shiftKey ? last : first)?.focus();}}}}><div className="react-confirm" role="alertdialog" aria-modal="true" aria-labelledby="center-confirm-title"><h2 id="center-confirm-title">{confirmation.title}</h2><p>{confirmation.text}</p><div><CenterButton autoFocus onClick={() => setConfirmation(null)}>继续保留</CenterButton><CenterButton primary onClick={() => {const action = confirmation.action; setConfirmation(null); action();}}>{confirmation.accept}</CenterButton></div></div></div>}
  </div></section>;
}
export default KitchenCenters;
