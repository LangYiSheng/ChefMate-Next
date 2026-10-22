import { node as element } from './ui-components.js';
import { pretty, safeText } from './chat-http.js';
import { markdown } from './chat-markdown.js';
import { icon } from './icons.js';
import { brandImage } from './branding.js';
import { imageThumbnail } from './image-preview.js';
import { activityLabel } from './activity.js';
import { visibleMessageViews } from './workspace.js';
import { legacyRecipeItems } from './ui-protocol.js';
import { VIEW_LABELS, viewSummary, activityRows, latestActivity, messageViewsForStage } from './presentation.js';
import { foodPresentations } from './chat-food.js';

const labels = {
  find_ingredients: '查找食材', search_recipes: '查询菜谱', get_recipe: '读取菜谱',
  get_search_options: '读取查询选项', beginner: '新手', normal: '普通', advanced: '进阶',
  update_preferences: '更新偏好', present_preferences: '整理偏好', show_recipe_choices: '展示菜谱选择',
  build_meal_plan: '安排菜单', build_shopping_list: '整理备菜清单', prepare_ingredients: '准备食材',
  get_cooking_guide: '整理烹饪步骤', start_timer: '开始计时', load_skill: '读取组件指南', list_skills: '查看组件指南',
  complete: '完整', estimated: '含估算', incomplete: '不完整', unknown: '未知',
};

// Renders transcript messages and public execution traces without owning mutations.
export function createChatRenderer({
  state, ui, workspace, currentSession, cookingFocus, contextMeter, chatFood,
  busy, blankTitle, hasRetry, sendMessage, openRecipe, openDrafts, updateComposer, updateConversationState, renderQueryPanel
}) {
  function toolCallNode(call) {
    const node = element('details', 'tool-call');
    const name = call.name || call.tool_name || call.function?.name || '查询工具';
    const suffix = call.error ? ' · 失败' : call.running ? ' · 执行中' : call.status === 'interrupted' ? ' · 未完成' : '';
    node.append(element('summary', '', `${labels[name] || name}${suffix}`));
    node.append(element('div', 'tool-call-label', '输入条件'));
    node.append(element('pre', '', pretty(call.arguments ?? call.args ?? call.input ?? call.function?.arguments)));
    if (call.result !== undefined || call.output !== undefined) {
      node.append(element('div', 'tool-call-label', '返回数据'));
      node.append(element('pre', '', pretty(call.result ?? call.output)));
    }
    if (call.error) node.append(element('div', 'error-box', safeText(typeof call.error === 'string' ? call.error : call.error.message)));
    return node;
  }

  function executionTrace(message) {
    if (message.streaming && !state.developer) {
      const line = element('div','execution-trace trace-summary');
      line.append(activityLabel(safeText(latestActivity(message)),message.turn_id || message.id || message));
      return line;
    }
    const rows = activityRows(message);
    const trace = element(message.streaming || state.traceMode==='detailed' ? 'section' : 'details', `execution-trace ${state.traceMode}`);
    trace.setAttribute('aria-label','执行过程');
    const summary = element(trace.tagName==='DETAILS' ? 'summary' : 'div','trace-summary');
    if(message.streaming)summary.append(activityLabel(safeText(latestActivity(message)),message.turn_id || message.id || message));
    else summary.append(icon(message.error ? 'circle-alert' : message.cancelled ? 'square' : 'circle-check','trace-orbit'),element('span','trace-current','执行过程'));
    trace.append(summary);
    if (message.usage?.model_calls) {
      const usage = message.usage;
      const count = value => value == null ? '未报告' : Number(value).toLocaleString();
      trace.append(element('p','usage-summary',`${usage.model_calls} 次模型请求 · 输入 ${count(usage.input_tokens)} · 输出 ${count(usage.output_tokens)} · 缓存读取 ${count(usage.cached_tokens)} tokens`));
    }
    {
      const timeline = element('ol','trace-timeline');
      for (const row of rows) {
        const item = element('li',`trace-row ${row.state || 'note'}`);
        item.append(icon(row.state==='running' ? 'loader-circle' : row.state==='failed' ? 'circle-alert' : 'check','trace-marker'));
        const copy = element('div','trace-copy');
        if (row.kind === 'note') {
          const note = element('details','trace-note');
          note.append(element('summary','',safeText(row.text.split('\n')[0].replace(/\*\*/g,'').slice(0,120))),markdown(safeText(row.text)));
          copy.append(note);
        } else copy.append(element('p','',safeText(row.text)));
        if (row.kind === 'tool') copy.append(element('small','',({running:'使用中',failed:'未完成，正在调整',interrupted:'已中断',done:'已完成'})[row.state]));
        item.append(copy); timeline.append(item);
      }
      trace.append(timeline);
      if (message.tool_calls?.length) {
        const technical = element('details','trace-technical'); technical.append(element('summary','','查看工具记录'));
        for (const call of message.tool_calls) technical.append(toolCallNode(call));
        trace.append(technical);
      }
    }
    return trace;
  }

  function recipeId(recipe) { return recipe.recipe_id || recipe.id; }
  function matchedNames(recipe) {
    return [...new Set((recipe.matched_ingredients || []).map((item) => typeof item === 'string' ? item : item.canonical_name || item.name || item.ingredient_id).filter(Boolean))];
  }
  function timeLabel(value) { return value == null ? '耗时未知' : `${Number(value)} 分钟`; }

  function recipeButton(recipe, snapshot) {
    const button = element('button', 'recipe-result');
    button.type = 'button';
    button.setAttribute('aria-label', `查看${recipe.name || '菜谱'}详情`);
    const copy = element('span', 'recipe-result-copy');
    const title = element('span', 'recipe-result-name', recipe.name || '未命名菜谱');
    if (recipe.nutrition_status === 'estimated' || recipe.nutrition?.estimated || recipe.nutrition_estimated) {
      const marker=element('span','tag','营养估算');marker.dataset.estimateMarker='';title.append(marker);
    }
    copy.append(title);
    const names = matchedNames(recipe);
    const meta = [timeLabel(recipe.total_time_min), labels[recipe.difficulty] || recipe.difficulty, names.length ? `匹配 ${names.join('、')}` : ''].filter(Boolean);
    copy.append(element('span', 'recipe-result-meta', meta.join(' · ')));
    button.append(copy, icon('chevron-right','recipe-result-arrow'));
    button.disabled = !recipeId(recipe);
    const expectedSnapshot = snapshot ? { ...snapshot } : null;
    button.addEventListener('click', () => openRecipe(recipeId(recipe), expectedSnapshot, true));
    return button;
  }

  function renderConversation(forceScroll = false) {
    contextMeter.render(currentSession(),state.developer);
    const session = currentSession();
    const messages = session?.messages || [];
    const nearBottom = ui.conversation.scrollHeight - ui.conversation.scrollTop - ui.conversation.clientHeight < 100;
    const openedLogs = new Set([...ui.messages.querySelectorAll('details[open][data-log-key]')].map((node) => node.dataset.logKey));
    ui['chat-title'].textContent = ({journal:'食光手记',recipes:'菜谱中心',settings:'设置'})[ui.app.dataset.page]
      || (!blankTitle(session?.title) ? session.title : '新对话');
    ui.welcome.hidden = messages.length > 0;
    ui.messages.hidden = !messages.length;
    ui.messages.replaceChildren();
    const lastViewMessage = new Map();
    const foodOwners=new Map();messages.forEach((message,index)=>foodPresentations(message.tool_calls).records.forEach(record=>foodOwners.set(record.id,index)));
    const shownViews = visibleMessageViews;
    messages.forEach((message,index) => shownViews(message).forEach(view => lastViewMessage.set(view.id,index)));
    for (let index = 0; index < messages.length; index++) {
      const message = messages[index];
      if (message.role === 'user') {
        const node = element('article', 'message user');
        node.setAttribute('aria-label', '你的消息');
        if(message.images?.length) {
          const images=element('div','message-images');
          for(const [imageIndex,image] of message.images.entries()) images.append(imageThumbnail(state.currentId,image,message.images,imageIndex));
          node.append(images);
        }
        if(message.content)node.append(element('div', 'user-bubble', message.content));
        ui.messages.append(node);
        continue;
      }
      const node = element('article', 'message assistant');
      node.setAttribute('aria-label', 'ChefMate 的回复');
      const heading = element('div', 'assistant-heading');
      heading.append(brandImage('assistant-icon'), element('span', '', 'ChefMate'));
      node.append(heading);
      const body = element('div', 'assistant-body');
      const processOnly = message.streaming && !message.resultComplete || Boolean(message.error || message.cancelled);
      if (message.content && !processOnly) body.append(markdown(message.content));
      node.append(body);
      const typedViews = shownViews(message);
      if (typedViews.length) {
        const components = element('div', 'message-components');
        const relevant = messageViewsForStage(typedViews,workspace.entry(state.currentId).snapshot?.stage || session?.conversation_state?.stage);
        const inlineIds = new Set(relevant.filter(view=>view.placement==='inline').slice(-(Number(state.uiPreferences.inline_limit)===1?1:2)).map(view=>view.id));
        for (const view of relevant) {
          if (lastViewMessage.get(view.id) !== index) continue;
          const live = inlineIds.has(view.id) && workspace.renderInline(view,state.currentId,()=>scheduleRender(state.currentId));
          if (live) { live.classList.add('inline-component'); components.append(live); continue; }
          const receipt = element('button','view-receipt'); receipt.type='button';
          const glyph=icon(({recipe_choices:'layout-grid',preference_form:'list-filter',cooking_guide:'cooking-pot',timers:'timer'})[view.kind] || 'list');glyph.classList.add('view-receipt-icon');
          receipt.append(glyph,element('span','view-receipt-copy',view.title || VIEW_LABELS[view.kind]),element('small','',viewSummary(view)),icon('arrow-up-right'));
          receipt.addEventListener('click',()=>workspace.openCurrent(view.kind)); components.append(receipt);
        }
        node.append(components);
      }
      const recipeItems = message.streaming ? [] : legacyRecipeItems(message, typedViews);
      if (recipeItems.length) {
        const recipes = element('div', 'recipe-results');
        for (const recipe of recipeItems) recipes.append(recipeButton(recipe, message.snapshot));
        node.append(recipes);
      } else if (message.resultTotal === 0) node.append(element('div', 'empty-results', '没有找到同时满足这些条件的菜谱，可以继续调整条件。'));
      if (!message.streaming && message.tool_calls?.some(call=>call.name==='create_recipe_draft' && call.result?.status==='ok')) {
        const receipt=element('button','view-receipt','核对刚整理的菜谱草稿');receipt.type='button';
        receipt.onclick=()=>{openDrafts();};node.append(receipt);
      }
      if(!message.streaming){const food=chatFood.render(message,{sessionId:state.currentId,canAct:!busy(state.currentId)&&!state.preparing&&!state.creating,recordOwners:foodOwners,index});if(food)node.append(food);}
      if ((message.streaming || state.developer) && (message.tool_calls?.length || message.process?.length || message.streaming || message.usage?.model_calls)) {
        const log = executionTrace(message);
        log.dataset.logKey = `${state.currentId}:${index}`;
        for (const detail of log.querySelectorAll('details')) {
          detail.dataset.logKey = `${log.dataset.logKey}:${detail.querySelector('summary')?.textContent}`;
          detail.open = openedLogs.has(detail.dataset.logKey);
        }
        node.append(log);
      }
      if (message.error) {
        const error = element('div', 'error-box', safeText(message.error));
        if (hasRetry(message) && !busy()) {
          const retry = element('button', 'retry-button', '重试这次请求');
          retry.type = 'button';
          retry.addEventListener('click', () => sendMessage(message.retryMessage, !message.retryAsNew, message));
          error.append(element('br'), retry);
        }
        node.append(error);
      }
      if (message.cancelled) {
        const note = element('div', 'message-meta', '本次执行已停止。');
        if (hasRetry(message) && !busy()) {
          const retry = element('button', 'retry-button', '重新执行');
          retry.type = 'button'; retry.addEventListener('click', () => sendMessage(message.retryMessage, !message.retryAsNew, message));
          note.append(document.createTextNode(' '), retry);
        }
        node.append(note);
      }
      ui.messages.append(node);
    }
    if (nearBottom || forceScroll) ui.conversation.scrollTop = ui.conversation.scrollHeight;
    updateConversationState();
    cookingFocus.refresh();
  }

  function scheduleRender(id) {
    if (id !== state.currentId || state.renderFrame) return;
    state.renderFrame = requestAnimationFrame(() => {
      state.renderFrame = null;
      if (id !== state.currentId) return;
      renderConversation(); renderQueryPanel(); updateComposer();
    });
  }

  return { renderConversation, scheduleRender, toolCallNode };
}
