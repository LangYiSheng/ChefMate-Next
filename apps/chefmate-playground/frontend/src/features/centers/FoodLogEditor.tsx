import {useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {BasisNote, CenterButton, CenterField, CenterTabs, MetricGrid} from './CenterControls';
import {emptyFoodItem, macros, mealNames, numeric, nutritionKeys, updateFoodItem, validateFood, type Data} from './center-model';
import type {EditorProps} from './RecipeEditor';

export function FoodLogEditor({initial, onDirty, onBusy, onStatus, onSaved, onDelete}: EditorProps) {
  const [record, setRecord] = useState(initial), [draft, setDraft] = useState<Data>(() => structuredClone(initial.document)), [baseline, setBaseline] = useState(JSON.stringify(initial.document));
  const [tab, setTab] = useState('basic'), [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null), alive = useRef(true), working = useRef(false), manualFields = useRef(new Map<number, Set<string>>());
  const dirty = JSON.stringify(draft) !== baseline, saved = record.status === 'saved';
  useEffect(() => {onDirty(dirty);}, [dirty, onDirty]);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const change = (key: string, value: any) => {setDraft(previous => ({...previous, [key]: value})); onStatus('有未保存的修改');};
  const changeItem = (index: number, key: string, value: any) => {
    setDraft(previous => ({...previous, items: previous.items.map((item: Data, position: number) => {
      if (position !== index) return item;
      if (!nutritionKeys.includes(key)) {if (['name', 'portion', 'amount_g'].includes(key)) manualFields.current.delete(index); return updateFoodItem(item, key, value);}
      const changed = structuredClone(item); changed.nutrition[key] = numeric(value);
      const manual = manualFields.current.get(index) || new Set(); manual.add(key); manualFields.current.set(index, manual);
      const known = nutritionKeys.filter(key => changed.nutrition[key] !== null);
      changed.nutrition_basis = known.length ? item.nutrition_basis === 'user_provided' || known.every(key => manual.has(key)) ? 'user_provided' : 'estimated' : 'unknown';
      changed.evidence = changed.nutrition_basis === 'user_provided' ? '用户在核对页手动填写' : changed.nutrition_basis === 'estimated' ? '部分营养由用户在核对页修正，其余数值沿用原估算。' : '';
      if (changed.nutrition_basis === 'estimated' && !changed.assumptions.length) changed.assumptions = ['未手动修改的营养仍按原食物组成和份量估算。'];
      return changed;
    })}));
    onStatus(['name', 'portion'].includes(key) ? '食物或文字份量已修改，原营养已清空，请重新核对。' : '有未保存的修改');
  };
  const save = async (publish: boolean) => {
    if (working.current || saved) return;
    const invalid = Array.from(form.current?.querySelectorAll<HTMLInputElement>('input,textarea,select') || []).find(control => !control.checkValidity());
    if (invalid) {setTab(invalid.closest<HTMLElement>('[data-editor-tab]')?.dataset.editorTab || 'basic'); requestAnimationFrame(() => invalid.reportValidity()); return;}
    const error = validateFood(draft); if (error) {setTab(error.includes('依据') || error.includes('假设') ? 'source' : 'foods'); onStatus(error, true); return;}
    working.current = true; setBusy(true); onBusy(true); onStatus(publish ? '正在确认饮食记录…' : '正在保存饮食草稿…');
    try {
      let accepted = record;
      if (dirty) {
        const document = {...draft, items: draft.items.map((item: Data) => ({...item, name: item.name.trim(), portion: item.portion.trim(), assumptions: item.assumptions.filter((line: string) => line.trim())}))};
        accepted = await api(`/api/food-logs/${encodeURIComponent(record.id)}`, {method: 'PUT', body: JSON.stringify({revision: record.revision, record: document})});
        if (!alive.current) return;
        setRecord(accepted); setDraft(structuredClone(accepted.document)); setBaseline(JSON.stringify(accepted.document)); manualFields.current.clear();
      }
      if (publish) await api(`/api/food-logs/${encodeURIComponent(record.id)}/publish`, {method: 'POST', body: JSON.stringify({revision: accepted.revision})});
      if (alive.current) {onStatus(publish ? '饮食已记录，个人营养统计已更新。' : '饮食草稿已保存，确认后才会计入统计。'); onSaved(publish);}
    } catch (reason) {if (alive.current) onStatus((reason as Error).message, true);}
    finally {working.current = false; if (alive.current) {setBusy(false); onBusy(false);}}
  };
  const field = (label: string, key: string, props: any = {}) => <CenterField key={key} label={label} value={draft[key]} disabled={busy} onChange={value => change(key, value)} {...props}/>;
  const itemField = (index: number, label: string, key: string, props: any = {}) => <CenterField key={key} label={label} value={nutritionKeys.includes(key) ? draft.items[index].nutrition[key] : draft.items[index][key]} disabled={busy} onChange={value => changeItem(index, key, value)} {...props}/>;
  return <form ref={form} className="center-editor-form" onSubmit={event => {event.preventDefault(); void save(false);}}>
    <CenterTabs items={[["basic", saved ? '这次饮食' : '基本信息'], ["foods", "食物与营养"], ["source", "来源与依据"]]} selected={tab} onSelect={setTab}/>
    <section hidden={tab !== 'basic'} data-editor-tab="basic">{saved ? <><p className="center-muted">{record.date} · {mealNames[draft.meal_type]} · 已确认的个人摄入</p><h3>{draft.title}</h3><p className="center-source-text">{draft.notes}</p></> : <><p className="center-muted">只记录你实际吃的这一份。核对后确认，才会计入个人营养统计。</p>{field('记录名称', 'title', {required: true, maxLength: 120})}{field('日期', 'date', {type: 'date', required: true, onChange: (value: string) => {setDraft(previous => ({...previous, date: value, date_evidence: '用户在核对页选择日期'}));}})}{field('餐次', 'meal_type', {options: Object.entries(mealNames)})}{field('备注', 'notes', {type: 'textarea', maxLength: 2000})}</>}</section>
    <section hidden={tab !== 'foods'} data-editor-tab="foods"><p className="center-muted">每项为你吃下的这一份总量。未知可留空；修改食物或文字份量后会清空原营养。</p>{draft.items.map((item: Data, index: number) => <section className="food-log-item" key={index}><header className="food-log-item-header"><h3>{item.name || `食物 ${index + 1}`}</h3>{!saved && <CenterButton disabled={busy || draft.items.length <= 1} onClick={() => {change('items', draft.items.filter((_: Data, position: number) => position !== index)); manualFields.current.clear();}}>移除食物</CenterButton>}</header>{saved ? <><p className="center-muted">{item.portion || '份量未知'}{Number.isFinite(item.amount_g) ? ` · ${item.amount_g} 克` : ''}</p><MetricGrid nutrition={item.nutrition}/></> : <><div className="food-log-item-grid">{itemField(index, '食物名称', 'name', {required: true, maxLength: 120})}{itemField(index, '我吃的份量', 'portion', {maxLength: 160, hint: '例如半碗、一小盘，或实际称量的克重。'})}{itemField(index, '克重（可留空）', 'amount_g', {type: 'number', min: .001, max: 10000, step: 'any'})}</div><div className="food-log-macros">{macros.map(([key, label, unit]) => itemField(index, `${label}（${unit}）`, key, {type: 'number', min: 0, max: key === 'calories_kcal' ? 20000 : 3000, step: 'any'}))}</div></>}<BasisNote item={item}/></section>)}{!saved && <CenterButton disabled={busy || draft.items.length >= 30} onClick={() => change('items', [...draft.items, emptyFoodItem()])}>添加食物</CenterButton>}</section>
    <section hidden={tab !== 'source'} data-editor-tab="source">{record.document.source_kind === 'image' && <><h3>来源照片</h3><div className="food-log-source-images">{(record.source_image_ids || []).map((id: string) => <figure className="food-log-source-image" key={id}><img src={`/api/sessions/${encodeURIComponent(record.source_session_id)}/images/${encodeURIComponent(id)}`} alt="本次饮食记录的来源照片" loading="lazy" onError={event => {event.currentTarget.hidden = true;}}/><figcaption>来源照片</figcaption></figure>)}{!record.source_image_ids?.length && <p>原图已清理，饮食记录仍保留。</p>}</div></>}{saved ? draft.source_text && <><h3>原始描述</h3><p className="center-source-text">{draft.source_text}</p></> : field('原始描述', 'source_text', {type: 'textarea', maxLength: 8000})}{draft.items.map((item: Data, index: number) => <section className="food-log-item" key={index}><h3>{item.name || `食物 ${index + 1}`}</h3><BasisNote item={item}/>{saved ? <><p className="center-source-text">{item.evidence}</p>{item.assumptions.map((text: string, index: number) => <p className="center-warning" key={index}>{text}</p>)}</> : <>{itemField(index, '营养依据', 'evidence', {type: 'textarea', maxLength: 1000})}<CenterField label="估算假设（每行一条）" type="textarea" value={item.assumptions.join('\n')} disabled={busy} maxLength={4812} onChange={value => changeItem(index, 'assumptions', value.split('\n'))} hint="保留碗盘大小、食材组成、用油等估算依据；最多 12 条。"/></>}</section>)}</section>
    <div className="center-editor-actions"><CenterButton danger disabled={busy} onClick={() => onDelete(record)}>移到回收站</CenterButton>{!saved && <><CenterButton disabled={busy} onClick={() => save(false)}>保存草稿</CenterButton><CenterButton primary disabled={busy} onClick={() => save(true)}>确认记录</CenterButton></>}</div>
  </form>;
}
