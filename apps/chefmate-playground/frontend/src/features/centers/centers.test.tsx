import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {KitchenCenters} from './KitchenCenters';
import {FoodLogEditor} from './FoodLogEditor';
import {RecipeEditor} from './RecipeEditor';
import {emptyFoodItem, intakeMetric, recipeDraft, updateFoodItem, updateRecipeIngredient, updateRecipeMetadata, validateFood} from './center-model';
const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const callbacks = () => ({onDirty: vi.fn(), onBusy: vi.fn(), onStatus: vi.fn(), onSaved: vi.fn(), onDelete: vi.fn()});
const recipe = () => ({recipe_id: 'custom-1', revision: 2, status: 'draft', source_kind: 'text', source_text: '鸡蛋 2 个，煮熟', time_evidence: '', document: {name: '煮蛋', servings: null, servings_evidence: '', total_time_min: null, prep_time_min: null, cook_time_min: null, ingredients: [{canonical_name: '鸡蛋', raw_amount: '2', raw_unit: '个', grams: 100, amount_evidence: '2 个', scalable: true, usda_query: 'egg', allergens: ['egg']}], steps: ['煮熟'], warnings: []}});
const suggestedRecipe = () => ({...recipe(), source_text: '鸡蛋 2 个，煮熟\n备菜5分钟，烹饪10分钟',
  time_evidence: '备菜5分钟，烹饪10分钟；总耗时：助手建议；用户请求：请补充难度、人数和总耗时',
  difficulty_evidence: '助手建议；用户请求：请补充难度、人数和总耗时',
  source_metadata_evidence: {servings: '', difficulty: '', time: '备菜5分钟，烹饪10分钟'},
  metadata_provenance: Object.fromEntries(['servings', 'difficulty', 'total_time_min'].map(field => [field, {kind: 'suggested', evidence: '请补充难度、人数和总耗时', question_id: null}])),
  document: {...recipe().document, servings: 1, servings_evidence: '助手建议；用户请求：请补充难度、人数和总耗时', difficulty: 'normal', prep_time_min: 5, cook_time_min: 10, total_time_min: 30}});
const food = () => ({...emptyFoodItem(), name: '米饭', portion: '100 克', amount_g: 100, portion_basis: 'user_provided', nutrition: {...emptyFoodItem().nutrition, calories_kcal: 120, protein_g: 3}, nutrition_basis: 'estimated', evidence: '按米饭估算', assumptions: ['按普通熟米饭估算']});
beforeEach(() => {request.mockReset();});
describe('center data invariants', () => {
  it('restores original evidence and conversation provenance through the real recipeDraft conversion', () => {
    const record = suggestedRecipe(), draft = recipeDraft(record);
    expect(draft.metadata_provenance).toEqual(record.metadata_provenance);
    expect(draft).toMatchObject({servings_evidence: '', difficulty_evidence: '', time_evidence: '备菜5分钟，烹饪10分钟'});
    draft.metadata_provenance.servings.evidence = '改变副本';
    expect(record.metadata_provenance.servings.evidence).toBe('请补充难度、人数和总耗时');
  });
  it.each([['servings', 2, 'servings_evidence', '份数：2人份'], ['difficulty', 'beginner', 'difficulty_evidence', '难度：新手'], ['total_time_min', 60, 'time_evidence', '备菜5分钟，烹饪10分钟\n总耗时：60分钟']] as const)('changes only manually edited %s provenance and quotes the actual supplement', (field, value, evidenceField, evidence) => {
    const reference = recipeDraft(suggestedRecipe()), changed = updateRecipeMetadata(reference, field, value, reference);
    expect(changed.error).toBe(''); expect(changed.draft[field]).toBe(value);
    expect(changed.draft.metadata_provenance[field]).toBeUndefined();
    for (const other of ['servings', 'difficulty', 'total_time_min'].filter(key => key !== field)) expect(changed.draft.metadata_provenance[other]).toEqual(reference.metadata_provenance[other]);
    expect(changed.draft[evidenceField]).toBe(evidence); expect(changed.draft.source_text).toContain(evidence);
    expect(changed.draft.source_text.startsWith(reference.source_text)).toBe(true);
    const restored = updateRecipeMetadata(changed.draft, field, reference[field], reference, changed.notes);
    expect(restored.draft).toEqual(reference); expect(JSON.stringify(restored.draft)).toBe(JSON.stringify(reference));
  });
  it('preserves source prep and cooking times when clearing only the total time', () => {
    const reference = recipeDraft(suggestedRecipe()), changed = updateRecipeMetadata(reference, 'total_time_min', null, reference);
    expect(changed.draft).toMatchObject({total_time_min: null, prep_time_min: 5, cook_time_min: 10, time_evidence: '备菜5分钟，烹饪10分钟'});
    expect(changed.draft.source_text).toBe(reference.source_text);
    expect(changed.draft.metadata_provenance.total_time_min).toBeUndefined();
    expect(changed.draft.metadata_provenance.servings).toEqual(reference.metadata_provenance.servings);
  });
  it('invalidates source grams and USDA associations only when ingredient identity changes', () => {
    const previous = {name: '鸡蛋', amount: '2个', grams: 100, amount_evidence: '2个', scalable: true, usda_query: 'egg', allergens: ['egg']};
    expect(updateRecipeIngredient(previous, 'amount', '3个')).toMatchObject({grams: null, amount_evidence: '', usda_query: 'egg', allergens: ['egg']});
    expect(updateRecipeIngredient(previous, 'name', '番茄')).toMatchObject({grams: null, usda_query: '', allergens: []});
    expect(updateRecipeIngredient(previous, 'name', ' 鸡蛋 ', previous)).toMatchObject({grams: 100, usda_query: 'egg', allergens: ['egg']});
    const renamed = updateRecipeIngredient(previous, 'name', '番茄', previous);
    expect(updateRecipeIngredient(renamed, 'name', '鸡蛋', previous)).toMatchObject({grams: 100, amount_evidence: '2个', usda_query: 'egg'});
  });
  it('keeps unknown nutrients null while scaling grams and invalidates nutrition on text edits', () => {
    const scaled = updateFoodItem(food(), 'amount_g', '200');
    expect(scaled.nutrition).toMatchObject({calories_kcal: 240, protein_g: 6, fat_g: null}); expect(scaled.nutrition_basis).toBe('estimated'); expect(scaled.portion).toBe('200 克');
    const renamed = updateFoodItem(food(), 'name', '面条'); expect(renamed.amount_g).toBeNull(); expect(Object.values(renamed.nutrition).every(value => value === null)).toBe(true); expect(renamed.nutrition_basis).toBe('unknown');
    const summary = intakeMetric({total: {protein_g: null}, known_total: {protein_g: 0}}, 'protein_g'); expect(summary).toMatchObject({value: 0, partial: true});
    const reference = {...food(), nutrition: {...food().nutrition, calories_kcal: 100.001}};
    const reduced = updateFoodItem(reference, 'amount_g', '33', reference);
    expect(updateFoodItem(reduced, 'amount_g', '100', reference).nutrition.calories_kcal).toBe(100.001);
  });
  it('requires real portions and evidence before confirming known nutrition', () => {
    expect(validateFood({title: '午饭', items: [{...food(), portion: ''}]})).toContain('份量');
    expect(validateFood({title: '午饭', items: [{...food(), evidence: ''}]})).toContain('依据');
    expect(validateFood({title: '午饭', items: [food()]})).toBe('');
  });
});

describe('center request lifecycles', () => {
  it('saves a name-only edit from a provenance-bearing record without turning display evidence into source facts', async () => {
    const initial = suggestedRecipe();
    request.mockResolvedValue({...initial, revision: 3, document: {...initial.document, name: '家常煮蛋'}});
    const handlers = callbacks(); render(<RecipeEditor initial={initial} {...handlers}/>);
    fireEvent.change(screen.getByLabelText('菜名'), {target: {value: '家常煮蛋'}});
    fireEvent.click(screen.getByRole('button', {name: '保存草稿'}));
    await waitFor(() => expect(handlers.onSaved).toHaveBeenCalledWith(false));
    const saved = JSON.parse(request.mock.calls[0][1].body).recipe;
    expect(saved.metadata_provenance).toEqual(initial.metadata_provenance);
    expect(saved).toMatchObject({name: '家常煮蛋', source_text: initial.source_text, servings_evidence: '', difficulty_evidence: '', time_evidence: '备菜5分钟，烹饪10分钟'});
  });
  it('writes a manual total-time edit as source text while preserving the other accepted metadata', async () => {
    const initial = suggestedRecipe(); request.mockResolvedValue({...initial, revision: 3});
    const handlers = callbacks(); render(<RecipeEditor initial={initial} {...handlers}/>);
    fireEvent.change(screen.getByLabelText('总耗时（分钟）'), {target: {value: '60'}});
    fireEvent.click(screen.getByRole('button', {name: '保存草稿'}));
    await waitFor(() => expect(handlers.onSaved).toHaveBeenCalledWith(false));
    const saved = JSON.parse(request.mock.calls[0][1].body).recipe;
    expect(saved.total_time_min).toBe(60); expect(saved.metadata_provenance.total_time_min).toBeUndefined();
    expect(saved.metadata_provenance.servings).toEqual(initial.metadata_provenance.servings);
    expect(saved.metadata_provenance.difficulty).toEqual(initial.metadata_provenance.difficulty);
    expect(saved.time_evidence).toBe('备菜5分钟，烹饪10分钟\n总耗时：60分钟');
    expect(saved.source_text).toBe(initial.source_text + '\n\n用户补充\n备菜5分钟，烹饪10分钟\n总耗时：60分钟');
  });
  it('preserves recipe metadata on unrelated edits and records manually chosen difficulty as user evidence', async () => {
    const initial = {...recipe(), document: {...recipe().document, taste_tags: ['清淡'], cooking_methods: ['煮'], required_tools: ['煮锅']}};
    request.mockImplementation((_path: string, options: RequestInit) => Promise.resolve({...initial, revision: 3, difficulty_evidence: JSON.parse(options.body as string).recipe.difficulty_evidence}));
    const handlers = callbacks(); render(<RecipeEditor initial={initial} {...handlers}/>);
    fireEvent.click(screen.getByRole('combobox', {name: '制作难度'}));
    fireEvent.click(screen.getByRole('option', {name: '新手'}));
    fireEvent.click(screen.getByRole('button', {name: '保存草稿'}));
    await waitFor(() => expect(handlers.onSaved).toHaveBeenCalledWith(false));
    const saved = JSON.parse(request.mock.calls[0][1].body).recipe;
    expect(saved).toMatchObject({difficulty: 'beginner', difficulty_evidence: '难度：新手', taste_tags: ['清淡'], cooking_methods: ['煮'], required_tools: ['煮锅']});
    expect(saved.source_text).toBe('鸡蛋 2 个，煮熟\n\n用户补充\n难度：新手');
    expect(saved.total_time_min).toBeNull();
  });
  it('retains accepted recipe revision when publish fails and retries publication without a second PUT', async () => {
    const initial = recipe(), accepted = {...initial, revision: 3, document: {...initial.document, name: '茶叶蛋'}}; let publications = 0;
    request.mockImplementation((_path: string, options: RequestInit) => {if (options.method === 'PUT') return Promise.resolve(accepted); publications++; return publications === 1 ? Promise.reject(new Error('发布未完成')) : Promise.resolve({...accepted, status: 'saved'});});
    const handlers = callbacks(); render(<RecipeEditor initial={initial} {...handlers}/>);
    fireEvent.change(screen.getByLabelText('菜名'), {target: {value: '茶叶蛋'}}); fireEvent.click(screen.getByRole('button', {name: '确认保存菜谱'}));
    await waitFor(() => expect(handlers.onStatus).toHaveBeenCalledWith('发布未完成', true));
    expect(screen.getByLabelText('菜名')).toHaveValue('茶叶蛋'); fireEvent.click(screen.getByRole('button', {name: '确认保存菜谱'}));
    await waitFor(() => expect(handlers.onSaved).toHaveBeenCalledWith(true));
    expect(request.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(1);
    const payloads = request.mock.calls.filter(([path]) => path.endsWith('/publish')).map(([, options]) => JSON.parse(options.body)); expect(payloads).toEqual([{revision: 3}, {revision: 3}]);
  });
  it('saves food edits with unknown values intact and publishes only the accepted revision', async () => {
    const record = {id: 'food-1', status: 'draft', revision: 7, date: '2026-10-23', document: {title: '午餐', date: '2026-10-23', date_evidence: '今天', meal_type: 'lunch', source_kind: 'text', source_text: '午饭', notes: '', items: [food()]}};
    const accepted = {...record, revision: 8, document: {...record.document, title: '我的午餐'}};
    request.mockImplementation((_path: string, options: RequestInit) => Promise.resolve(options.method === 'PUT' ? accepted : {...accepted, status: 'saved'}));
    const handlers = callbacks(); render(<FoodLogEditor initial={record} {...handlers}/>);
    fireEvent.change(screen.getByLabelText('记录名称'), {target: {value: '我的午餐'}}); fireEvent.click(screen.getByRole('button', {name: '确认记录'}));
    await waitFor(() => expect(handlers.onSaved).toHaveBeenCalledWith(true));
    expect(JSON.parse(request.mock.calls[0][1].body).record.items[0].nutrition.fat_g).toBeNull(); expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({revision: 8});
  });
  it('blocks external navigation from a dirty inspector and cleans up the event listener on unmount', async () => {
    const initial = recipe();
    request.mockImplementation((path: string) => Promise.resolve(path.startsWith('/api/cookbook?') ? {items: [{recipe_id: 'custom-1', name: '煮蛋', status: 'draft', origin: 'user'}], total: 1} : initial));
    const {unmount} = render(<KitchenCenters page="recipes"/>);
    fireEvent.click(await screen.findByRole('button', {name: '核对草稿'}));
    fireEvent.change(await screen.findByLabelText('菜名'), {target: {value: '未保存煮蛋'}});
    const resume = vi.fn(), event = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'journal', resume}});
    act(() => {window.dispatchEvent(event);}); expect(event.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole('button', {name: '继续保留'})); expect(screen.getByLabelText('菜名')).toHaveValue('未保存煮蛋'); expect(resume).not.toHaveBeenCalled();
    unmount(); const next = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'journal', resume}}); window.dispatchEvent(next); expect(next.defaultPrevented).toBe(false);
  });

  it('confirms a dirty internal return once when the parent also guards navigation', async () => {
    request.mockImplementation((path: string) => Promise.resolve(path.startsWith('/api/cookbook?') ? {items: [{recipe_id: 'custom-1', name: '煮蛋', status: 'draft', origin: 'user'}], total: 1} : recipe()));
    let navigation: CustomEvent | undefined;
    const onReturn = vi.fn(() => {navigation = new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'chat', resume: vi.fn()}}); window.dispatchEvent(navigation);});
    render(<KitchenCenters page="recipes" onReturn={onReturn}/>);
    fireEvent.click(await screen.findByRole('button', {name: '核对草稿'})); fireEvent.change(await screen.findByLabelText('菜名'), {target: {value: '还未保存'}});
    fireEvent.click(screen.getByRole('button', {name: '返回对话'})); fireEvent.click(screen.getByRole('button', {name: '放弃修改'}));
    expect(onReturn).toHaveBeenCalledOnce(); expect(navigation?.defaultPrevented).toBe(false); expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('starts cooking through the chat workflow instead of merely opening another detail pane', () => {
    const record = {...recipe(), status: 'saved'}, handlers = callbacks(), onChat = vi.fn(), onRecipe = vi.fn();
    render(<RecipeEditor initial={record} {...handlers} onChat={onChat} onRecipe={onRecipe}/>);
    fireEvent.click(screen.getByRole('button', {name: '就做这道菜'}));
    expect(onChat).toHaveBeenCalledWith('我想做我保存的自定义菜谱「煮蛋」。请先检索并读取真实菜谱，再帮我安排。'); expect(onRecipe).not.toHaveBeenCalled();
  });

  it('does not expose a delete action on a foundation recipe', () => {
    const record = {...recipe(), recipe_id: 'catalog-1', status: 'saved'}; render(<RecipeEditor initial={record} {...callbacks()}/>);
    expect(screen.queryByRole('button', {name: '移到回收站'})).not.toBeInTheDocument();
  });

  it('ignores a late recipe detail after opening a different recipe', async () => {
    const responses: Record<string, (value: any) => void> = {};
    request.mockImplementation((path: string) => path.startsWith('/api/cookbook?') ? Promise.resolve({items: [{recipe_id: 'custom-1', name: '煮蛋', status: 'draft', origin: 'user'}, {recipe_id: 'custom-2', name: '炒蛋', status: 'draft', origin: 'user'}], total: 2}) : new Promise(resolve => {responses[path] = resolve;}));
    render(<KitchenCenters page="recipes"/>); fireEvent.click(await screen.findByRole('button', {name: '核对煮蛋'})); fireEvent.click(screen.getByRole('button', {name: '核对炒蛋'}));
    await act(async () => {responses['/api/cookbook/custom-2']({...recipe(), recipe_id: 'custom-2', document: {...recipe().document, name: '炒蛋'}});});
    expect(await screen.findByLabelText('菜名')).toHaveValue('炒蛋'); await act(async () => {responses['/api/cookbook/custom-1'](recipe());}); expect(screen.getByLabelText('菜名')).toHaveValue('炒蛋');
  });

  it('updates the inspector title after accepting an edited draft', async () => {
    const initial = recipe(), accepted = {...initial, revision: 3, document: {...initial.document, name: '新菜名'}};
    request.mockImplementation((path: string, options: RequestInit = {}) => Promise.resolve(options.method === 'PUT' ? accepted : path.startsWith('/api/cookbook?') ? {items: [{recipe_id: 'custom-1', name: '煮蛋', status: 'draft', origin: 'user'}], total: 1} : initial));
    render(<KitchenCenters page="recipes"/>); fireEvent.click(await screen.findByRole('button', {name: '核对草稿'})); fireEvent.change(await screen.findByLabelText('菜名'), {target: {value: '新菜名'}}); fireEvent.click(screen.getByRole('button', {name: '保存草稿'}));
    await screen.findByRole('heading', {name: '新菜名'}); expect(screen.getByLabelText('菜名')).toHaveValue('新菜名');
  });
});
