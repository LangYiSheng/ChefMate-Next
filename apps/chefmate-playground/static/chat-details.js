import { api, safeText } from './chat-http.js';
import { node as element, renderView } from './ui-components.js';
import { icon } from './icons.js';

const methods = { steam: '蒸', boil: '煮', stir_fry: '炒', deep_fry: '炸', pan_fry: '煎', bake: '烘焙', roast: '烤', stew: '炖', braise: '焖烧', cold_mix: '凉拌', other: '其他' };
const tools = { stove: '炉灶', wok: '炒锅', pot: '锅', oven: '烤箱', air_fryer: '空气炸锅', microwave: '微波炉', rice_cooker: '电饭煲', steamer: '蒸锅', pressure_cooker: '高压锅' };

// Owns sidebar surfaces, source recipe reads and the developer/component detail panels.
export function createDetailController({
  state, ui, currentSession, developerPanel, loadStatus, announce, toolCallNode
}) {
  function resetRecipe() {
    state.detailEpoch++;
    state.detailAbort?.abort();
    state.detailAbort = null;
    state.selectedRecipe = null;
    renderRecipeEmpty();
  }
  function backdrop() {
    const mobile = matchMedia('(max-width: 760px)').matches;
    const overlayDetail = matchMedia('(max-width: 1180px)').matches;
    ui.sidebar.inert = mobile && !state.sidebarOpen;
    if (mobile && !state.sidebarOpen) ui.sidebar.setAttribute('aria-hidden', 'true');
    else ui.sidebar.removeAttribute('aria-hidden');
    ui['drawer-backdrop'].hidden = !(mobile && state.sidebarOpen || overlayDetail && state.detailOpen);
  }
  function setSidebar(open) {
    state.sidebarOpen = open;
    ui.app.classList.toggle('sidebar-open', open);
    ui['open-sidebar'].setAttribute('aria-expanded', String(open));
    backdrop();
    if (open) ui['new-chat'].focus();
  }
  function setDetails(open) {
    state.detailOpen = open;
    ui.app.classList.toggle('panel-open', open);
    ui['detail-panel'].hidden = !open;
    ui['toggle-details'].setAttribute('aria-expanded', String(open));
    ui['toggle-details'].setAttribute('aria-label', open ? '收起右侧栏' : '展开右侧栏');
    backdrop();
  }
  function setTab(tab, focus = false) {
    if (!state.developer && ['query','components'].includes(tab)) tab='workspace';
    state.tab = tab;
    for (const name of ['workspace', 'query', 'recipe', 'components']) {
      ui[`${name}-tab`].classList.toggle('active', name === tab);
      ui[`${name}-tab`].setAttribute('aria-selected', String(name === tab));
      ui[`${name}-tab`].tabIndex = name === tab ? 0 : -1;
      ui[`${name}-content`].hidden = name !== tab;
    }
    if (focus) ui[`${tab}-tab`].focus();
    if (tab === 'components') renderComponentsPanel();
    if (tab === 'query') renderQueryPanel();
  }

  function panelSection(title) {
    const section = element('section', 'panel-section');
    section.append(element('h3', '', title));
    return section;
  }

  function renderQueryPanel() {
    if(state.developer){developerPanel.render();return;}
    const opened = new Set([...ui['query-content'].querySelectorAll('details[open][data-call-index]')].map((node) => node.dataset.callIndex));
    ui['query-content'].replaceChildren();
    const status = state.status;
    const connection = panelSection('当前连接');
    const db = status?.database;
    const rows = [
      ['模型', status?.configured ? status.model || '已配置' : '未配置'],
      ['数据库', db?.ready ? '已连接' : '未连接'],
      ['菜谱数量', db?.recipe_count ?? db?.recipes_count ?? '未知'],
      ['数据版本', db?.data_version || '未知'],
    ];
    for (const [key, value] of rows) {
      const row = element('div', 'connection-line'); row.append(element('span', '', key), element('span', '', safeText(value))); connection.append(row);
    }
    if (!status?.ready) {
      const retry = element('button', 'panel-retry', '重新检查连接');
      retry.type = 'button'; retry.addEventListener('click', loadStatus); connection.append(retry);
    }
    ui['query-content'].append(connection);
    const messages = currentSession()?.messages || [];
    const latest = [...messages].reverse().find((message) => message.role === 'assistant');
    const lastSearch = [...messages].reverse().flatMap((message) => [...(message.tool_calls || [])].reverse())
      .find((call) => (call.name || call.tool_name) === 'search_recipes' && call.result);
    if (lastSearch && !(latest?.tool_calls || []).includes(lastSearch)) {
      const evidence = panelSection('最近一次菜谱查询'); evidence.append(toolCallNode(lastSearch)); ui['query-content'].append(evidence);
    }
    if (!latest?.tool_calls?.length) {
      const empty = element('div', 'panel-empty');
      empty.append(icon('search','panel-empty-symbol'), element('div', '', '查询开始后，这里会保留食材候选、筛选条件和返回依据。'));
      ui['query-content'].append(empty); return;
    }
    const section = panelSection('本轮查询');
    if (latest.snapshot?.data_version) section.append(element('p', 'nutrition-note', `本轮数据版本 ${latest.snapshot.data_version}`));
    latest.tool_calls.forEach((call, index) => {
      const node = toolCallNode(call); node.dataset.callIndex = String(index); node.open = opened.has(String(index)); section.append(node);
    });
    ui['query-content'].append(section);
  }

  function renderRecipeEmpty() {
    ui['recipe-content'].replaceChildren();
    const empty = element('div', 'panel-empty');
    empty.append(icon('book-open','panel-empty-symbol'), element('div', '', '点击回复中的菜谱，查看食材用量、完整步骤和营养信息。'));
    ui['recipe-content'].append(empty);
  }

  async function loadComponents(id = state.currentId) {
    const epoch = ++state.componentsEpoch;
    try {
      const payload = await api('/api/components');
      if (epoch !== state.componentsEpoch || id !== state.currentId) return;
      state.components = Array.isArray(payload) ? payload : payload?.skills || payload?.items || [];
      renderComponentsPanel();
    } catch (error) {
      if (epoch === state.componentsEpoch && id === state.currentId) {
        ui['components-content'].replaceChildren(element('div', 'error-box', error.message));
      }
    }
  }

  function renderComponentsPanel() {
    ui['components-content'].replaceChildren();
    const heading = element('div', 'workspace-heading'); heading.append(element('span', 'workspace-stage', '系统组件'));
    const refresh = element('button', 'ui-text-button', '刷新'); refresh.type = 'button'; refresh.addEventListener('click', () => loadComponents()); heading.append(refresh);
    ui['components-content'].append(heading, element('p', 'ui-hint', '组件统一绑定指南、工具与界面。指南在需要时加载。'));
    const latest = [...(currentSession()?.messages || [])].reverse().find((message) => message.role === 'assistant');
    const loaded = new Set();
    for (const skill of latest?.skills || []) loaded.add(skill.id || skill.name || skill.skill_id);
    for (const action of latest?.actions || []) if (action.kind === 'skill_loaded') loaded.add(action.skill_id || action.skill?.id || action.skill?.name);
    for (const call of latest?.tool_calls || []) if (call.name === 'load_skill' && call.result?.status === 'ok') loaded.add(call.arguments?.name || call.arguments?.skill_id || call.result?.skill?.id || call.result?.skill?.name);
    if (!state.components.length) ui['components-content'].append(element('div', 'panel-empty', '目前没有可用系统组件。'));
    for (const skill of state.components) {
      const card = element('details', 'skill-card'); const summary = element('summary');
      const title = skill.name || skill.id || '系统组件';
      const isLoaded = loaded.has(skill.id) || loaded.has(skill.name) || Boolean(skill.loaded);
      summary.append(element('strong', '', title), element('span', 'ui-badge muted', skill.enabled === false ? '已关闭' : isLoaded ? '本轮指南已读取' : '已启用'));
      card.append(summary, element('p', '', skill.description || '暂无说明'));
      const details = [skill.scope ? `来源：${skill.scope}` : '', skill.version ? `版本：${skill.version}` : '', skill.digest ? `内容版本：${skill.digest.slice(0, 12)}` : ''].filter(Boolean);
      if (details.length) card.append(element('p', 'ui-hint', details.join(' · ')));
      const loadedCall = latest?.tool_calls?.find((call) => call.name === 'load_skill' && call.arguments?.name === skill.id && call.result?.status === 'ok');
      const content = skill.body || skill.content || loadedCall?.result?.content;
      if (content) card.append(element('pre', '', content));
      ui['components-content'].append(card);
    }
  }

  function detailRequest(id, snapshot, fromReference) {
    if (fromReference && !snapshot) throw new Error('这条菜谱引用缺少数据版本，请重新查询。');
    if (snapshot) return {
      path: '/api/tools/get_recipe',
      options: { method: 'POST', body: JSON.stringify({ recipe_id: id, expected_snapshot: snapshot }) },
    };
    return { path: `/api/recipes/${encodeURIComponent(id)}`, options: {} };
  }

  async function openRecipe(id, expectedSnapshot = null, fromReference = false) {
    const epoch = ++state.detailEpoch;
    const sessionId = state.currentId;
    state.detailAbort?.abort();
    const controller = new AbortController();
    state.detailAbort = controller;
    state.selectedRecipe = id;
    setDetails(true); setTab('recipe');
    ui['recipe-content'].replaceChildren(element('div', 'panel-loading', '正在读取菜谱…'));
    try {
      const request = detailRequest(id, expectedSnapshot, fromReference);
      const payload = await api(request.path, { ...request.options, signal: controller.signal });
      if (epoch !== state.detailEpoch || sessionId !== state.currentId || id !== state.selectedRecipe) return;
      if (payload?.status === 'snapshot_changed') {
        ui['recipe-content'].replaceChildren(element('div', 'error-box', '数据已更新，请重新查询。'));
        announce('菜谱数据已更新，请重新查询。');
        return;
      }
      if (payload?.status && payload.status !== 'ok') throw new Error('这道菜谱暂时不可用，请重新查询。');
      const recipe = payload?.recipe || payload;
      if (!recipe || !recipe.name) throw new Error('这道菜谱暂时不可用。');
      renderRecipe(recipe, payload.snapshot);
      announce(`已打开${recipe.name}的详情。`);
    } catch (error) {
      if (error.name === 'AbortError' || epoch !== state.detailEpoch || sessionId !== state.currentId) return;
      const errorNode = element('div', 'error-box', safeText(error.message));
      const retry = element('button', 'retry-button', '重试读取');
      retry.type = 'button'; retry.addEventListener('click', () => openRecipe(id, expectedSnapshot, fromReference));
      if (!fromReference || expectedSnapshot) errorNode.append(element('br'), retry);
      ui['recipe-content'].replaceChildren(errorNode);
    }
  }

  function amountLabel(item) {
    if (item.raw_amount || item.raw_unit) return [item.raw_amount, item.raw_unit].filter(Boolean).join(' ');
    if (item.grams != null) return `${Number(item.grams)} g`;
    return '用量未知';
  }
  function renderRecipe(recipe, snapshot, viewState = {}) {
    const shown = {name:recipe.name,description:recipe.description,servings:recipe.servings,
      original_servings:recipe.servings,estimated_servings:recipe.estimated_servings,
      total_time_min:recipe.total_time_min,difficulty:recipe.difficulty,
      ingredients:(recipe.ingredients || []).map(item=>({canonical_name:item.canonical_name || item.raw_name || item.ingredient_id,
        amount_text:amountLabel(item),estimated:item.amount_evidence==='estimated' || item.estimated_grams})),
      steps:(recipe.steps || []).map(step=>({instruction:typeof step==='string' ? step : step.description || step.text})),
      nutrition:recipe.nutrition || {per_serving:recipe.nutrition_per_serving},
      references:recipe.references || [],
      warnings:[...(recipe.warnings || []),...(recipe.blocking_reasons || []),
        ...(recipe.required_tools?.length ? [`所需厨具：${recipe.required_tools.map(v=>tools[v] || v).join('、')}`] : []),
        ...(recipe.cooking_methods?.length ? [`烹饪方式：${recipe.cooking_methods.map(v=>methods[v] || v).join('、')}`] : [])],
      source:{data_snapshot:snapshot}};
    ui['recipe-content'].replaceChildren(renderView({id:'source-detail',kind:'recipe_detail',title:'菜谱详情',revision:0,editable:false,data:{recipe:shown}},
      {historical:false,busy:false,viewState,onViewState:()=>renderRecipe(recipe,snapshot,viewState),action:async()=>{}}));
  }

  return { resetRecipe, backdrop, setSidebar, setDetails, setTab, renderQueryPanel, renderRecipeEmpty, loadComponents, renderComponentsPanel, openRecipe };
}
