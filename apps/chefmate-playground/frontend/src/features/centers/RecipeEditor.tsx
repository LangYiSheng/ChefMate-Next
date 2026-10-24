import {useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {CenterButton, CenterField, CenterTabs, MetricGrid} from './CenterControls';
import {lines, numeric, recipeDraft, updateRecipeIngredient, updateRecipeMetadata, type Data, type RecipeMetadataField, type RecipeMetadataNotes} from './center-model';

export type EditorProps = {initial: Data; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void; onStatus: (text: string, error?: boolean) => void; onSaved: (published: boolean) => void; onDelete: (record: Data) => void; onRecord?: (record: Data) => void; onRecipe?: (id: string) => void; onChat?: (text: string) => void};
export function RecipeEditor({initial, onDirty, onBusy, onStatus, onSaved, onDelete, onRecord, onRecipe, onChat}: EditorProps) {
  const [record, setRecord] = useState(initial), [draft, setDraft] = useState<Data>(() => initial.status === 'draft' ? recipeDraft(initial) : {}), [baseline, setBaseline] = useState(() => initial.status === 'draft' ? JSON.stringify(recipeDraft(initial)) : '{}');
  const [tab, setTab] = useState(initial.status === 'draft' ? 'basic' : 'steps'), [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null), alive = useRef(true), working = useRef(false), ingredientReferences = useRef<Data[] | null>(null);
  const metadataReference = useRef<Data | null>(null), metadataNotes = useRef<RecipeMetadataNotes>({});
  if (metadataReference.current === null) metadataReference.current = initial.status === 'draft' ? recipeDraft(initial) : {};
  if (ingredientReferences.current === null) ingredientReferences.current = initial.status === 'draft' ? structuredClone(recipeDraft(initial).ingredients) : [];
  const dirty = JSON.stringify(draft) !== baseline;
  useEffect(() => {onDirty(dirty);}, [dirty, onDirty]);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const change = (key: string, value: any) => {setDraft(previous => ({...previous, [key]: value})); onStatus('有未保存的修改');};
  const changeMetadata = (field: RecipeMetadataField, value: any) => {
    const next = updateRecipeMetadata(draft, field, value, metadataReference.current!, metadataNotes.current);
    if (next.error) {onStatus(next.error, true); return;}
    metadataNotes.current = next.notes; setDraft(next.draft); onStatus('有未保存的修改');
  };
  const changeIngredient = (index: number, key: string, value: string) => change('ingredients', draft.ingredients.map((item: Data, position: number) => position === index ? updateRecipeIngredient(item, key, value, ingredientReferences.current?.[index] || item) : item));
  const removeIngredient = (index: number) => {ingredientReferences.current?.splice(index, 1); change('ingredients', draft.ingredients.filter((_: Data, position: number) => position !== index));};
  const addIngredient = () => {const item = {name: '', amount: '适量', grams: null, amount_evidence: '', scalable: true, usda_query: '', allergens: []}; ingredientReferences.current?.push(structuredClone(item)); change('ingredients', [...draft.ingredients, item]);};
  const save = async (publish: boolean) => {
    if (working.current) return;
    const invalid = Array.from(form.current?.querySelectorAll<HTMLInputElement>('input,textarea,select') || []).find(control => !control.checkValidity());
    if (invalid) {setTab(invalid.closest<HTMLElement>('[data-editor-tab]')?.dataset.editorTab || 'basic'); requestAnimationFrame(() => invalid.reportValidity()); return;}
    const steps = lines(draft.steps.join('\n')), warnings = lines(draft.warnings.join('\n'));
    if (!steps.length || steps.length > 80 || steps.some(step => step.length > 2000)) {setTab('method'); onStatus('请填写 1–80 个做法步骤，每步不超过 2000 字。', true); return;}
    if (!draft.ingredients.length || draft.ingredients.length > 80) {setTab('method'); onStatus('请保留 1–80 项食材。', true); return;}
    if (warnings.length > 30) {setTab('source'); onStatus('注意事项最多 30 条。', true); return;}
    working.current = true; setBusy(true); onBusy(true); onStatus(publish ? '正在确认保存菜谱…' : '正在保存草稿…');
    try {
      let accepted = record;
      if (dirty) {
        accepted = await api(`/api/cookbook/${encodeURIComponent(record.recipe_id)}`, {method: 'PUT', body: JSON.stringify({revision: record.revision, recipe: {...draft, steps, warnings}})});
        if (!alive.current) return;
        const nextDraft = recipeDraft(accepted); ingredientReferences.current = structuredClone(nextDraft.ingredients);
        metadataReference.current = structuredClone(nextDraft); metadataNotes.current = {};
        setRecord(accepted); setDraft(nextDraft); setBaseline(JSON.stringify(nextDraft)); onRecord?.(accepted);
      }
      if (publish) await api(`/api/cookbook/${encodeURIComponent(record.recipe_id)}/publish`, {method: 'POST', body: JSON.stringify({revision: accepted.revision})});
      if (alive.current) {onStatus(publish ? '菜谱已保存，可以开始做啦。' : '草稿已保存。'); onSaved(publish);}
    } catch (reason) {if (alive.current) onStatus((reason as Error).message, true);}
    finally {working.current = false; if (alive.current) {setBusy(false); onBusy(false);}}
  };
  const document = record.document || initial.recipe || initial;
  const recipe = {...document, nutrition: record.nutrition || document.nutrition};
  const field = (label: string, key: string, props: any = {}) => <CenterField key={key} label={label} value={draft[key]} onChange={value => change(key, value)} disabled={busy} {...props}/>;
  if (record.status !== 'draft') return <>
    <CenterTabs items={[["steps", "做法"], ["ingredients", "食材"], ["nutrition", "营养"], ["source", "来源"]]} selected={tab} onSelect={setTab}/>
    <div hidden={tab !== 'steps'}><ol className="center-recipe-steps">{(recipe.steps || []).map((step: any, index: number) => <li key={index}>{typeof step === 'string' ? step : step.instruction || step.description}</li>)}</ol>{!recipe.steps?.length && <p>暂时没有做法记录。</p>}</div>
    <div hidden={tab !== 'ingredients'}><ul className="center-ingredients">{(recipe.ingredients || []).map((food: Data, index: number) => <li key={index}><span>{food.canonical_name || food.raw_text}</span><span className="center-muted">{food.raw_amount ? `${food.raw_amount}${food.raw_unit || ''}` : food.raw_text || '适量'}</span></li>)}</ul></div>
    <div hidden={tab !== 'nutrition'}><h3>原配方每份营养</h3><MetricGrid nutrition={recipe.nutrition?.per_serving}/><p className="center-muted">{recipe.nutrition?.estimated && <span className="estimate-marker">含估算。</span>}按原配方计算，未知项保留空缺。</p></div>
    <div hidden={tab !== 'source'}><h3>{record.recipe_id?.startsWith('custom-') ? '你的个人菜谱' : '菜谱库原配方'}</h3><p className="center-muted">{Number.isFinite(recipe.servings) ? `原配方 ${recipe.servings} 份` : '原份量待确认'} · {Number.isFinite(recipe.total_time_min) ? `${recipe.total_time_min} 分钟` : '总耗时待确认'}</p>{record.source_text && <><h3>来源原文</h3><p className="center-source-text">{record.source_text}</p></>}{(recipe.warnings || []).map((warning: string, index: number) => <p className="center-warning" key={index}>{warning}</p>)}</div>
    <div className="center-editor-actions">{record.recipe_id?.startsWith('custom-') && <CenterButton danger onClick={() => onDelete(record)}>移到回收站</CenterButton>}<CenterButton primary onClick={() => onChat ? onChat(`我想做${record.recipe_id?.startsWith('custom-') ? '我保存的自定义菜谱' : '菜谱中心里的'}「${recipe.name}」。请先检索并读取真实菜谱，再帮我安排。`) : onRecipe?.(record.recipe_id || recipe.recipe_id)}>就做这道菜</CenterButton></div>
  </>;
  return <form className="center-editor-form" ref={form} onSubmit={event => {event.preventDefault(); void save(false);}}>
    <CenterTabs items={[["basic", "基本信息"], ["method", "食材与做法"], ["source", "来源与提示"]]} selected={tab} onSelect={setTab}/>
    <section hidden={tab !== 'basic'} data-editor-tab="basic"><p className="center-muted">核对名字、用量与做法。保存到“我的菜谱”后，就能让助手安排烹饪。</p>{field('菜名', 'name', {required: true, maxLength: 100})}{field('原配方份数', 'servings', {type: 'number', min: .1, max: 100, step: 'any', hint: '未知可以留空；手动填写会作为你的补充记入来源。', onChange: (value: string) => changeMetadata('servings', numeric(value))})}{field('份数依据', 'servings_evidence')}{field('总耗时（分钟）', 'total_time_min', {type: 'number', min: 0, max: 2880, step: 'any', hint: '未知可以留空；手动填写会作为整道菜的总耗时补充。', onChange: (value: string) => changeMetadata('total_time_min', numeric(value))})}{field('耗时依据', 'time_evidence')}<CenterField label="制作难度" value={draft.difficulty || ''} options={[["", "未标注"], ["beginner", "新手"], ["normal", "普通"], ["advanced", "进阶"]]} onChange={value => changeMetadata('difficulty', value || null)} disabled={busy} hint="你手动选择的难度会作为补充记入来源；没有把握可以保持未标注。"/></section>
    <section hidden={tab !== 'method'} data-editor-tab="method"><h3>食材与用量</h3><div className="center-ingredient-editor">{draft.ingredients.map((food: Data, index: number) => <div className="center-ingredient-row" key={index}><input className="center-input" value={food.name} required maxLength={100} aria-label={`食材 ${index + 1} 名称`} disabled={busy} onChange={event => changeIngredient(index, 'name', event.target.value)}/><input className="center-input" value={food.amount} maxLength={200} aria-label={`食材 ${index + 1} 用量`} disabled={busy} onChange={event => changeIngredient(index, 'amount', event.target.value)}/><CenterButton icon aria-label={`移除食材 ${index + 1}`} disabled={busy} onClick={() => removeIngredient(index)}>×</CenterButton></div>)}</div><CenterButton disabled={busy || draft.ingredients.length >= 80} onClick={addIngredient}>添加食材</CenterButton><CenterField label="做法（每行一步）" value={draft.steps.join('\n')} type="textarea" disabled={busy} onChange={value => change('steps', value.split('\n'))}/></section>
    <section hidden={tab !== 'source'} data-editor-tab="source">{field('来源原文', 'source_text', {type: 'textarea', required: true, maxLength: 20000, hint: '可修正截图辨认错误，份数、耗时和用量依据需要能在这里找到。'})}<CenterField label="注意事项（每行一条）" value={draft.warnings.join('\n')} type="textarea" disabled={busy} onChange={value => change('warnings', value.split('\n'))}/></section>
    <div className="center-editor-actions"><CenterButton danger disabled={busy} onClick={() => onDelete(record)}>移到回收站</CenterButton><CenterButton disabled={busy} onClick={() => save(false)}>保存草稿</CenterButton><CenterButton primary disabled={busy} onClick={() => save(true)}>确认保存菜谱</CenterButton></div>
  </form>;
}
