// Public execution activity and view navigation. Never reads provider reasoning fields.
export const VIEW_LABELS = Object.freeze({preference_form:'条件',recipe_choices:'选菜',recipe_detail:'详情',
  meal_plan:'餐单',prep_checklist:'备菜',cooking_guide:'做法',timers:'计时'});
export const STAGE_NAMES = Object.freeze({idea:'闲聊',planning:'正在选菜',shopping:'备菜',prep:'备菜',cooking:'烹饪',completed:'已完成'});
export function conversationLabel(status={}) {
  const names=status.recipe_names || [];
  const name=status.active_recipe_name || names[0];
  if(name) return `${status.stage==='planning' ? '已选菜' : STAGE_NAMES[status.stage] || '已选菜'} - ${name}${names.length>1 ? ` · 另${names.length-1}道` : ''}`;
  return status.choosing || status.stage==='planning' ? '正在选菜' : STAGE_NAMES[status.stage] || '闲聊';
}
export function snapshotStatus(snapshot={}) {
  const recipes=snapshot.components?.find(view=>view.kind==='meal_plan')?.data.recipes || [];
  return {stage:snapshot.stage,recipe_names:recipes.map(recipe=>recipe.name),
    active_recipe_name:recipes.find(recipe=>recipe.recipe_id===snapshot.active_recipe_id)?.name,
    choosing:snapshot.components?.some(view=>['preference_form','recipe_choices','recipe_detail'].includes(view.kind))};
}
export const TOOL_NAMES = Object.freeze({find_ingredients:'识别食材',search_recipes:'筛选菜谱',get_recipe:'读取菜谱',
  get_search_options:'核对查询条件',show_preference_form:'整理用餐条件',show_recipe_choices:'整理菜品选择',
  show_recipe_details:'展示菜品详情',set_meal_plan:'安排餐单',show_meal_plan:'核对餐单',show_shopping_list:'整理备菜清单',
  show_prep_checklist:'整理备菜清单',show_cooking_guide:'整理烹饪步骤',set_cooking_timer:'设置计时',show_timers:'查看计时',
  get_cooking_workspace:'核对当前进度',advance_cooking_stage:'更新会话阶段',update_cooking_guidance:'调整当前做法',
  update_preferences:'保存用餐条件',select_recipes:'更新菜品选择',update_prep_items:'确认备菜状态',ask_user:'向你确认细节',load_skill:'加载厨房技能',list_skills:'查看厨房技能'});
export function viewChoices(views, stage) {
  const preferred = {idea:'preference_form',planning:'meal_plan',shopping:'prep_checklist',prep:'prep_checklist',cooking:'cooking_guide',completed:'meal_plan'}[stage];
  return views.filter(v => VIEW_LABELS[v.kind] && !(v.kind === 'recipe_choices' && ['prep','cooking','completed'].includes(stage)))
    .sort((a,b) => Number(b.kind === preferred)-Number(a.kind === preferred));
}
export function pageWindow(items, page = 0, size = 4) {
  const pages = Math.max(1,Math.ceil(items.length/size));
  const index = Math.max(0,Math.min(pages-1,Number.isFinite(page) ? Math.floor(page) : 0));
  return {index,pages,items:items.slice(index*size,(index+1)*size),total:items.length};
}
export function viewSummary(view) {
  const data = view.data || {};
  if (data.recipes) return `${data.recipes.length} 道菜 · ${data.recipes.map(r=>r.name).slice(0,3).join('、')}`;
  if (data.recipe) return data.recipe.name;
  if (data.items) return `${data.items.length} 项 · ${data.items.slice(0,3).map(i=>i.canonical_name).join('、')}`;
  if (data.steps) return `${data.recipe_name || '烹饪'} · ${data.steps.length} 个步骤`;
  if (data.timers) return `${data.timers.length} 个计时器`;
  if (view.kind === 'preference_form') return '食材、时间和用餐偏好';
  return STAGE_NAMES[data.stage] || '已更新工作区';
}
export function activityRows(message) {
  const rows = (message.process || []).filter(text=>typeof text === 'string' && text.trim())
    .map((text,index)=>({id:`text:${index}`,kind:'note',text}));
  for (const call of message.tool_calls || []) {
    const name = call.name || call.tool_name;
    const resultStatus = call.result?.status;
    const failed = Boolean(call.error) || Boolean(resultStatus && resultStatus !== 'ok');
    const running = call.running || call.status === 'running';
    const interrupted = call.status === 'interrupted' || (running && Boolean(message.cancelled || message.error));
    rows.push({id:call.call_id || `${name}:${rows.length}`,kind:'tool',text:TOOL_NAMES[name] || '使用厨房工具',
      state:interrupted ? 'interrupted' : running ? 'running' : failed ? 'failed' : 'done',call});
  }
  return rows;
}
export function latestActivity(message) {
  if (message.cancelled) return '本轮已停止';
  if (message.error) return '执行遇到问题 · 可以重试';
  const rows = activityRows(message);
  const running = rows.filter(r=>r.state==='running');
  if (running.length) return `${running[0].text}${running.length>1 ? ` · 另有 ${running.length-1} 项` : ''}…`;
  const last = rows.at(-1);
  if (message.streaming) return last ? last.text : message.content ? '正在回复…' : '正在理解你的需求…';
  return rows.length ? `已完成 ${rows.filter(r=>r.kind==='tool').length} 项操作` : '';
}
