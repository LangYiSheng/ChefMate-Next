import { node } from "./ui-components.js";
import { applyBranding } from "./branding.js";
import { readAppearance, applyAppearance } from './appearance.js';
import { TimerSound, readKitchenPreferences, saveKitchenPreferences } from './timer-sound.js';
import { icon } from './icons.js';
import {navigationIndex, navigationEdges, revealNavigationItem} from './settings-navigation.js';
import {enhanceControl} from './form-controls.js';
import {SettingsInsights} from './settings-insights.js';
import {TOOL_NAMES} from './presentation.js';
const reminderSound = new TimerSound();
applyBranding();
const embedded = window.parent !== window && new URLSearchParams(location.search).get('embedded') === '1';
document.body.classList.toggle('settings-embedded', embedded);
document.querySelectorAll('[data-icon]').forEach(node => node.replaceChildren(icon(node.dataset.icon)));
function notifyParent(type) { if (embedded) window.parent.postMessage({ type }, location.origin); }

const $ = (id) => document.getElementById(id);
const names = {
  model: ["模型连接", "选择模型、端点和连接方式。保存后用于接下来的对话。"],
  display: ["外观与对话", "按你的习惯使用小锅搭子。"],
  interaction: ["对话建议", "让下一步更好开口，也保留你自己的表达。"],
  cooking: ["烹饪与计时", "让计时和步骤切换更符合你的做饭习惯。"],
  memory: ["长期记忆", "留下口味、忌口和做饭习惯，让每一次建议更懂你。"],
  developer: ["开发者", "按需查看公开执行记录、工具参数和模型实际用量。"],
  components: ["系统组件", "统一管理指南、工具和交互界面。"],
  usage: ['Token 用量','查看模型服务已报告的使用量。'],
  about: ['关于 ChefMate',''],
  runtime: ["执行设置", "设置一次任务的等待时间和操作上限。"],
  context: ["上下文预算", "给查询结果和连续对话留出足够空间。"],
  data: ["数据与状态", "查看当前菜谱库和本地工作台状态。"],
};
let current = "display",
  loaded = null,
  status = null,
  busy = false,
  busyMessage = '正在处理设置…',
  testing = false;
const drafts = new Map(),
  draftRevisions = new Map(),
  dirty = new Set();
const selectedTabs = new Map();
let memoryHost = null;
let memorySaving = false;
let closeWaitMessage = '';
let insights = null;
window.addEventListener('chefmate:memory-busy', event => {
  memorySaving = event.detail?.busy === true;
  updateFooter();
});
window.addEventListener('chefmate:memory-dirty', event => {
  if (event.detail?.dirty === true) dirty.add('memory');
  else if (event.detail?.dirty === false) dirty.delete('memory');
  updateFooter();
});
const navigation = document.querySelector('.settings-nav');
const advancedNavigation=navigation.querySelector('.settings-advanced');
const advancedButtons=[...advancedNavigation.querySelectorAll('[data-section]')];
function updateNavigation(ensureCurrent=false) {
  const horizontal=window.matchMedia('(max-width:760px)').matches;
  for(const button of advancedButtons){if(horizontal && button.parentElement!==navigation)navigation.insertBefore(button,advancedNavigation);else if(!horizontal && button.parentElement!==advancedNavigation)advancedNavigation.append(button);}
  advancedNavigation.hidden=horizontal;
  if(!horizontal && advancedButtons.some(button=>button.dataset.section===current))advancedNavigation.open=true;
  const edges=horizontal?navigationEdges(navigation.scrollLeft,navigation.clientWidth,navigation.scrollWidth):{left:false,right:false};
  navigation.dataset.overflowLeft=String(edges.left);navigation.dataset.overflowRight=String(edges.right);
  if(!ensureCurrent || !horizontal)return;
  const selected=navigation.querySelector('[aria-current="page"]');if(!selected)return;
  const navRect=navigation.getBoundingClientRect(),itemRect=selected.getBoundingClientRect();
  const left=revealNavigationItem({scrollLeft:navigation.scrollLeft,viewportLeft:navRect.left,viewportWidth:navigation.clientWidth,
    itemLeft:itemRect.left,itemRight:itemRect.right,maximum:Math.max(0,navigation.scrollWidth-navigation.clientWidth)});
  if(Math.abs(left-navigation.scrollLeft)>1)navigation.scrollTo({left,behavior:window.matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'});
}
function readDisplay() {
  try {
    const { inline_limit = 2, density = "comfortable", theme = 'system' } = readAppearance();
    return { inline_limit, density, theme };
  } catch {
    return { inline_limit: 2, density: "comfortable", theme: 'system' };
  }
}
function readDeveloper() {
  try { return { enabled: false, trace_mode: "compact", ...JSON.parse(localStorage.getItem("chefmate:developer-settings") || "{}") }; }
  catch { return { enabled: false, trace_mode: "compact" }; }
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : "输入无效，请检查设置。",
    );
  return result;
}
function message(text, ok = false) {
  $("settings-message").hidden = !text;
  $("settings-message").className = ok ? "settings-success" : "settings-error";
  $("settings-message").textContent = text;
}
function changed() {
  dirty.add(current);
  updateFooter();
  message("");
}
function updateFooter() {
  if (closeWaitMessage && !busy && !testing && !memorySaving) {
    if ($("settings-message").textContent === closeWaitMessage) message('');
    closeWaitMessage = '';
  }
  const readOnly = ['data','memory','usage','about'].includes(current);
  document.querySelector('.settings-footer').hidden = current === 'memory';
  $("save-settings").hidden = readOnly;
  $("reload-settings").hidden = current === 'memory';
  $("reload-settings").textContent = ['usage','about'].includes(current)?'刷新':'重新读取';
  $("reload-settings").disabled = busy || testing;
  $("save-settings").disabled =
    !loaded || !dirty.has(current) || busy || testing;
  $("save-state").textContent = testing
    ? "正在验证原生工具调用…"
    : busy
      ? busyMessage
      : dirty.has(current)
        ? "当前页有未保存修改"
        : current==='usage'?'按服务端报告统计':current==='about'?'ChefMate 本地工作台':"设置已同步";
  if ($("test-model")) $("test-model").disabled = busy || testing;
  const fieldsLocked = busy || testing || current === 'memory' && memorySaving;
  for (const input of $("settings-form").querySelectorAll('input, select, textarea')) {
    input.disabled = fieldsLocked;
    input._syncControl?.();
  }
  document.querySelectorAll("[data-section]").forEach((button) => {
    button.setAttribute(
      "aria-current",
      button.dataset.section === current ? "page" : "false",
    );
  });
  requestAnimationFrame(()=>updateNavigation(true));
}
function heading(text) {
  $("settings-form").append(node("h2", "", text));
}
function selectSettingsTab(index, focus = false) {
  const buttons = [...$("settings-tabs").querySelectorAll('[role="tab"]')];
  if (!buttons[index]) return;
  selectedTabs.set(current, index);
  buttons.forEach((button, item) => {
    const selected = item === index;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(button.getAttribute('aria-controls')).hidden = !selected;
  });
  if (focus) buttons[index].focus();
}
function organizeSections() {
  const form = $("settings-form"), tabs = $("settings-tabs");
  tabs.replaceChildren(); tabs.hidden = true;
  const children = [...form.children];
  if (children.filter(child => child.tagName === 'H2').length < 2) return;
  let panel;
  for (const child of children) {
    if (child.tagName === 'H2') {
      const index = tabs.children.length;
      panel = node('section', 'settings-pane'); panel.id = `settings-pane-${index}`;
      panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', `settings-tab-${index}`);
      const button = node('button', 'settings-tab', child.textContent); button.type = 'button';
      button.id = `settings-tab-${index}`; button.setAttribute('role', 'tab');
      button.setAttribute('aria-controls', panel.id);
      button.addEventListener('click', () => selectSettingsTab(index));
      button.addEventListener('keydown', event => {
        const next = navigationIndex(event.key, index, tabs.children.length);
        if (next === null) return;
        event.preventDefault(); selectSettingsTab(next, true);
      });
      tabs.append(button); form.append(panel); child.hidden = true;
    }
    panel?.append(child);
  }
  tabs.hidden = false;
  selectSettingsTab(Math.min(selectedTabs.get(current) || 0, tabs.children.length - 1));
}
function reportSettingsValidity() {
  const form = $("settings-form");
  const invalid = [...form.elements].find(input => input.willValidate && !input.validity.valid);
  const panel = invalid?.closest('.settings-pane');
  if (panel) selectSettingsTab([...form.querySelectorAll('.settings-pane')].indexOf(panel));
  return form.reportValidity();
}
function field(
  label,
  key,
  {
    type = "text",
    note = "",
    hint = "",
    options = null,
    min = null,
    max = null,
  } = {},
) {
  const value = drafts.get(current);
  const row = node("div", `setting-field${type === 'checkbox' ? ' setting-switch-field' : ''}`),
    copy = node("label", "setting-label", label),
    control = node("span", "setting-control");
  const input = node(options ? "select" : "input");
  input.name = key;
  input.id = `setting-${key}`;
  copy.htmlFor = input.id;
  const descriptions = [];
  if (note) {
    const description = node('small', '', note); description.id = `${input.id}-note`;
    copy.append(description); descriptions.push(description.id);
  }
  if (options) {
    for (const [id, text] of options) {
      const option = node("option", "", text);
      option.value = id;
      input.append(option);
    }
    input.value = String(value[key]);
  } else {
    input.type = type;
    if (type === "checkbox") input.checked = Boolean(value[key]);
    else input.value = String(value[key] ?? "");
    if (min !== null) input.min = min;
    if (max !== null) input.max = max;
  }
  if (type === "password") {
    input.autocomplete = "new-password";
    input.placeholder = loaded.api_key_set
      ? "已保存密钥；留空沿用同一端点的密钥"
      : "填写这个供应商的专用 API Key";
  }
  if (["model", "base_url"].includes(key)) input.required = true;
  if (type === 'number' && !options) input.required = true;
  input.addEventListener(
    options || type === "checkbox" ? "change" : "input",
    () => {
      value[key] =
        type === "checkbox"
          ? input.checked
          : type === "number"
            ? input.value === '' ? '' : Number(input.value)
            : input.value;
      changed();
    },
  );
  control.append(enhanceControl(input, {label}));
  if (hint) {
    const description = node('small', '', hint); description.id = `${input.id}-hint`;
    (type === 'checkbox' ? copy : control).append(description); descriptions.push(description.id);
  }
  if (descriptions.length) input.setAttribute('aria-describedby', descriptions.join(' '));
  row.append(copy, control);
  $("settings-form").append(row);
  return input;
}
function render() {
  insights?.dispose();insights=null;
  document.querySelector('.settings-content').scrollTop = 0;
  $("section-title").textContent = names[current][0];
  $("section-description").textContent = names[current][1];
  $("settings-form").replaceChildren();
  $("settings-tabs").replaceChildren(); $("settings-tabs").hidden = true;
  message("");
  if (!loaded) {
    $("settings-form").append(node("p", "settings-note", "正在读取配置…"));
    return;
  }
  if (current === "model") {
    heading("连接信息");
    field("API 端点", "base_url", { hint: "OpenAI 兼容端点通常以 /v1 结尾。" });
    field("模型名称", "model");
    field("API Key", "api_key", {
      type: "password",
      hint: "留空沿用同一端点的已保存密钥。",
    });
    heading("连接方式");
    field("支持图片输入", "supports_images", {type:"checkbox",note:"按模型实际能力开启，用于识图与拍照记餐。"});
    field("同时接收的图片上限", "max_images", {type:"number", min:1, max:12,
      hint:"1–12 张，包含当前请求保留的历史图片。"});
    field("请求协议", "api_format", {
      options: [
        ["chat_completions", "OpenAI · Chat Completions"],
        ["responses", "OpenAI · Responses"],
      ],
    });
    field("流式工具调用", "stream_tool_calls", {
      type: "checkbox",
      note: "连接测试会检查所选方式。",
    });
    const button = node(
      "button",
      "secondary-button",
      testing ? "正在验证连接…" : "测试连接与工具调用",
    );
    button.type = "button";
    button.id = "test-model";
    button.disabled = testing || busy;
    button.addEventListener("click", testConnection);
    $("settings-form").append(button);
  } else if (current === "interaction") {
    heading("推荐回复");
    field("显示推荐回复", "reply_suggestions", {type:"checkbox",
      note:"每次回复结束后，提供三条左右的接话思路。",
      hint:"点击只替换输入框里的文字，你可以修改后再发送。关闭后也会隐藏历史回复中的建议。"});
  } else if (current === "cooking") {
    heading("烹饪计时");
    field('到时声音提醒', 'timer_sound', {type:'checkbox',
      note:'页面打开时播放提示音，音量跟随设备。'});
    const preview = node('button','secondary-button','试听提醒声'); preview.type = 'button';
    preview.addEventListener('click', async () => {
      const played = await reminderSound.play({preview:true});
      message(played ? '已播放提醒声。' : '浏览器暂未允许播放声音，请检查页面声音权限或设备音量。', played);
    });
    $("settings-form").append(preview);
    field("同时进行的计时器", "timer_limit", {type:"number",options:[[1,"单计时器"],[3,"最多 3 个"]],
      hint:"运行和暂停计入上限；已有计时会保留。"});
    field("切换步骤时清除上一步计时", "clear_timer_on_step_change", {type:"checkbox",
      note:"仅在进入下一实际步骤时清除，浏览步骤不影响计时。"});
  } else if (current === "developer") {
    heading("调试信息");
    field("开发者模式", "enabled", { type: "checkbox", note: "显示上下文占用、系统组件、工具和脱敏请求记录。" });
    field("记录展示方式", "trace_mode", {
      options: [
        ["compact", "收起记录 · 按需展开"],
        ["detailed", "展开公开执行轨迹"],
      ],
      note: "只展示公开操作与工具状态。",
    });
    $("settings-form").append(node("p", "settings-note", "上下文按实际请求分类统计，Token分解为估算；实际用量以服务端报告为准。"));
  } else if (current === "display") {
    heading('外观主题');
    field('主题', 'theme', {options:[['system','跟随系统'],['light','浅色'],['dark','深色']],
      hint:'聊天、做菜和设置同步切换。'});
    heading("核心内容");
    field("每次回复展开几项内容", "inline_limit", {
      options: [
        [1, "只展开一项，例如食材清单"],
        [2, "最多两项，例如餐单和食材清单"],
      ],
      hint: "更多内容仍可在工作区打开。",
    });
    field("内容间距", "density", {
      options: [
        ["comfortable", "宽松 · 内容之间留白更多"],
        ["compact", "紧凑 · 同一屏显示更多内容"],
      ],
      hint:"仅调整留白，做菜模式自动放大文字。",
    });
    heading('对话建议');field('显示推荐回复','reply_suggestions',{type:'checkbox',note:'回复后提供接话思路，点击后仍可修改再发送。'});
  } else if (current === 'memory') {
    const initialize = !memoryHost;
    memoryHost ||= node('section','memory-settings'); memoryHost.id = 'memory-settings';
    $("settings-form").append(memoryHost);
    if (initialize) window.dispatchEvent(new CustomEvent('chefmate:memory-settings',{detail:{container:memoryHost}}));
  } else if (current === "components") {
    const examples={'meal-planning':'5个人，帮我搭配4道家常菜。','pantry-preparation':'这些菜要先准备什么？','cooking-guide':'这一步要计时多久？','recipe-authoring':'把这张菜谱截图整理成我的菜谱。','personal-memory':'记住我不吃香菜。','food-journal':'这是今天的午饭，帮我记一下。'};
    const pageNames={journal:'食光手记',recipes:'菜谱中心',memory:'长期记忆设置'},uiNames={preference_form:'用餐条件',recipe_choices:'菜品选择',recipe_detail:'菜谱详情',meal_plan:'餐单与份量',prep_checklist:'备菜清单',cooking_guide:'烹饪步骤',timers:'计时器',task_progress:'任务进度',food_log:'饮食记录',nutrition_summary:'营养统计'};
    $('settings-form').append(node('p','components-intro','决定小锅搭子能帮你做什么。关闭后保留已有记录。'));
    for (const component of loaded.components || []) {
      const entry=node('section','settings-component'),input=field(component.name,component.id,{type:'checkbox',note:component.description}),row=input.closest('.setting-field');entry.append(row);
      const details=node('details','settings-component-details'),summary=node('summary','','查看能力');details.append(summary);
      const body=node('div','settings-component-body');
      if(examples[component.id]){body.append(node('h3','','可以这样说'),node('p','component-example',examples[component.id]));}
      body.append(node('h3','','可执行功能'));const tools=node('ul','component-tool-list');for(const tool of component.tools || []){const item=node('li');item.append(node('span','',TOOL_NAMES[tool] || tool),node('code','',tool));tools.append(item);}body.append(tools);
      const interfaces=[...(component.ui?.kinds || []).map(key=>uiNames[key] || key),...(component.ui?.pages || []).map(key=>pageNames[key] || key)];
      body.append(node('h3','','界面与组成'),node('p','',interfaces.length?interfaces.join('、'):'在当前对话内使用'));
      const technical=node('dl','component-manifest');for(const [label,value]of [['组件ID',component.id],['指南',component.instructions?.entrypoint || '未提供'],['加载方式',component.instructions?.loading==='on_demand'?'按需读取':component.instructions?.loading || '未提供'],['依赖',(component.dependencies || []).join('、') || '无额外依赖'],['状态',component.available===false?'依赖尚未就绪':'已就绪']]){technical.append(node('dt','',label),node('dd','',value));}body.append(technical);details.append(body);entry.append(details);$('settings-form').append(entry);
    }
    $('settings-form').append(node('p','settings-note','开关同时控制组件指南与工具权限。'));
  } else if (['usage','about'].includes(current)) {
    const host=node('section','settings-insights');$('settings-form').append(host);insights=new SettingsInsights({host,api});insights.show(current);
  } else if (current === "context") {
    heading("信息容量");
    field("会话上下文上限", "max_history_chars", {
      type: "number", min: 4000, max: 2000000,
      hint: "字符，包含对话历史与本轮工具信息。默认 400,000。",
    });
    field("单次工具结果上限", "max_tool_result_chars", {
      type: "number", min: 2000, max: 200000,
      hint: "字符，控制每次查询送给模型的信息量。默认 60,000。",
    });
    $("settings-form").append(node("p", "settings-note", "这里按字符计量，和模型的 token 上限不同。提高预算可以容纳更多结果，也会增加每轮发送的信息量。"));
  } else if (current === "runtime") {
    heading("等待与重试");
    field("单轮任务时限", "turn_timeout_s", {
      type: "number",
      min: 10,
      max: 300,
      hint: "秒，10–300",
    });
    field("单次模型等待", "model_timeout_s", {
      type: "number",
      min: 10,
      max: 300,
      hint: "秒，10–300",
    });
    field("模型请求重试", "model_max_retries", {
      type: "number",
      min: 0,
      max: 2,
      hint: "0–2 次",
    });
    heading("操作预算");
    field("模型调用上限", "max_model_rounds", {
      type: "number",
      min: 2,
      max: 30,
      hint: "每轮 2–30 次",
    });
    field("工具调用上限", "max_tool_calls", {
      type: "number",
      min: 2,
      max: 50,
      hint: "每轮 2–50 次",
    });
  } else {
    heading("当前连接");
    for (const [label, text] of [
      ["模型", status?.model || "尚未配置"],
      ["菜谱库", status?.database?.ready ? "已连接" : "暂未连接"],
      ["菜谱数量", `${status?.database?.recipe_count || 0} 道`],
      ["数据版本", status?.database?.data_version || "未知"],
      ["进行中的对话", String(status?.active_sessions || 0)],
    ]) {
      const row = node("div", "status-row");
      row.append(node("span", "", label), node("span", "", text));
      $("settings-form").append(row);
    }
    heading("本机保存");
    $("settings-form").append(
      node(
        "p",
        "settings-note",
        "会话、厨房进度和计时器随当前任务保存。模型与组件设置保存在本机，界面显示偏好只作用于这个浏览器。",
      ),
    );
  }
  organizeSections();
  updateFooter();
}
async function load() {
  busyMessage = '正在读取设置…';
  busy = true;
  updateFooter();
  try {
    const result = await Promise.all([
      api("/api/settings"),
      api("/api/status"),
    ]);
    loaded = result[0];
    status = result[1];
    for (const section of ["model", "components", "runtime", "context", "interaction", "cooking", "display", "developer"])
      if (!dirty.has(section)) {
        draftRevisions.set(section,loaded.revision);
        drafts.set(
          section,
          section === "model"
            ? { ...loaded.model, api_key: "" }
            : section === "components"
              ? Object.fromEntries(
                  loaded.available_components.map((name) => [
                    name,
                    loaded.enabled_components.includes(name),
                  ]),
                )
              : section === "runtime"
                ? { ...loaded.runtime }
                : section === "context"
                  ? { ...loaded.context }
                : section === "interaction" ? {reply_suggestions:loaded.interaction?.reply_suggestions !== false}
                : section === "cooking" ? {timer_limit:loaded.cooking?.timer_limit===1?1:3,
                    clear_timer_on_step_change:loaded.cooking?.clear_timer_on_step_change===true,
                    ...readKitchenPreferences()}
                : section === "developer" ? readDeveloper() : {...readDisplay(),reply_suggestions:loaded.interaction?.reply_suggestions!==false},
        );
      }
    render();
  } catch (error) {
    message(error.message);
  } finally {
    busy = false;
    updateFooter();
  }
}
async function save() {
  if (busy || testing || !reportSettingsValidity()) return;
  const section = current,
    values = { ...drafts.get(section) };
  const timerSound = values.timer_sound;
  if(section==='cooking') { values.timer_limit=Number(values.timer_limit); delete values.timer_sound; }
  busyMessage = '正在保存修改…';
  busy = true;
  updateFooter();
  message("");
  try {
    if (section === "developer") {
      localStorage.setItem("chefmate:developer-settings", JSON.stringify(values));
    } else if (section === "display") {
      if(values.reply_suggestions!==loaded.interaction?.reply_suggestions){
        const revision=draftRevisions.get(section);loaded=await api('/api/settings',{method:'PATCH',body:JSON.stringify({revision,section:'interaction',values:{reply_suggestions:values.reply_suggestions}})});
        for(const [name,baseline] of draftRevisions)if(baseline===revision)draftRevisions.set(name,loaded.revision);
      }
      delete values.reply_suggestions;
      values.inline_limit = Number(values.inline_limit);
      localStorage.setItem("chefmate:ui-settings", JSON.stringify(values));
      applyBranding(); applyAppearance(values.theme);
    } else {
      const payload =
        section === "components"
          ? {
              enabled_components: Object.keys(values).filter(
                (name) => values[name],
              ),
            }
          : values;
      const revision = draftRevisions.get(section);
      loaded = await api("/api/settings", {
        method: "PATCH",
        body: JSON.stringify({
          revision,
          section,
          values: payload,
        }),
      });
      for(const [name,baseline] of draftRevisions) {
        if(baseline===revision) draftRevisions.set(name,loaded.revision);
      }
      if (section === "model") {
        drafts.set(section, { ...loaded.model, api_key: "" });
        const key = $("setting-api_key");
        if (key) key.value = "";
      }
      if (section === 'cooking') saveKitchenPreferences({timer_sound:timerSound});
    }
    dirty.delete(section);
    notifyParent('chefmate:settings-saved');
    if (current === section) {
      render();
      message(section==='cooking'?'计时设置已保存，对当前和新会话立即生效。':"设置已保存。接下来的对话使用新设置。", true);
    }
  } catch (error) {
    message(error.message);
  } finally {
    busy = false;
    updateFooter();
  }
}
async function testConnection() {
  if (testing || !reportSettingsValidity()) return;
  const payload = JSON.stringify(drafts.get("model"));
  testing = true;
  render();
  try {
    const result = await api("/api/settings/test-model", {
      method: "POST",
      body: payload,
    });
    if (current === "model")
      message(
        payload === JSON.stringify(drafts.get("model"))
          ? result.message
          : "连接设置在测试期间发生变化，请重新测试。",
        payload === JSON.stringify(drafts.get("model")) && result.ok,
      );
  } catch (error) {
    if (current === "model") message(error.message);
  } finally {
    testing = false;
    updateFooter();
    const button = [...$("settings-form").querySelectorAll("button")].find(
      (item) => item.textContent.includes("验证连接"),
    );
    if (button) {
      button.disabled = false;
      button.textContent = "测试连接与工具调用";
    }
  }
}
document.querySelectorAll("[data-section]").forEach((button) =>
  button.addEventListener("click", () => {
    current = button.dataset.section;
    render();
  }),
);
navigation.addEventListener('scroll',()=>updateNavigation(),{passive:true});
navigation.addEventListener('focusin',()=>requestAnimationFrame(()=>{
  const focused=document.activeElement;
  if(focused?.dataset.section && window.matchMedia('(max-width:760px)').matches) {
    const navRect=navigation.getBoundingClientRect(),itemRect=focused.getBoundingClientRect();
    const left=revealNavigationItem({scrollLeft:navigation.scrollLeft,viewportLeft:navRect.left,viewportWidth:navigation.clientWidth,
      itemLeft:itemRect.left,itemRight:itemRect.right,maximum:Math.max(0,navigation.scrollWidth-navigation.clientWidth)});
    navigation.scrollTo({left,behavior:'instant'});
  }
}));
navigation.addEventListener('keydown',event=>{
  if(!window.matchMedia('(max-width:760px)').matches)return;
  const buttons=[...navigation.querySelectorAll('[data-section]')];
  const focused=event.target.closest('[data-section]');
  const index=buttons.indexOf(focused || buttons.find(button=>button.dataset.section===current));
  const next=navigationIndex(event.key,index,buttons.length);if(next==null)return;
  event.preventDefault();buttons[next].focus({preventScroll:true});buttons[next].click();
});
window.addEventListener('resize',()=>updateNavigation(true));
if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>updateNavigation(true)).observe(navigation);
$("settings-form").addEventListener("submit", (event) => {
  event.preventDefault();
  save();
});
$("save-settings").addEventListener("click", save);
let pendingConfirmation=null;
function confirmInPage(description,action) {
  pendingConfirmation=action;
  $("confirm-description").textContent=description;
  $("settings-confirm").showModal();
}
$("cancel-confirm").addEventListener("click",()=>{
  pendingConfirmation=null;$("settings-confirm").close();
  notifyParent('chefmate:close-cancelled');
});
$("settings-confirm").addEventListener('cancel',()=>{pendingConfirmation=null;notifyParent('chefmate:close-cancelled');});
$("accept-confirm").addEventListener("click",()=>{
  const action=pendingConfirmation;pendingConfirmation=null;
  $("settings-confirm").close();action?.();
});
$("reload-settings").addEventListener("click", () => {
  if(insights && ['usage','about'].includes(current)){insights.show(current);return;}
  const section=current;
  const reload=()=>{
    dirty.delete(section);
    load();
  };
  if(dirty.has(section)) confirmInPage("放弃当前页未保存的修改，读取最新配置？其他分类的草稿会保留。",reload);
  else reload();
});
function requestClose() {
  if (busy || testing || memorySaving) {
    closeWaitMessage = memorySaving ? '正在保存记忆，完成后就可以返回。' : testing ? '正在测试连接，完成后就可以返回。' : '正在处理设置，完成后就可以返回。';
    message(closeWaitMessage);
    notifyParent('chefmate:close-cancelled');
    return;
  }
  const leave = () => embedded ? notifyParent('chefmate:close-settings') : location.assign('/');
  if (dirty.size) confirmInPage("返回对话会放弃尚未保存的设置。可以先保存，或者继续返回。", () => {
    dirty.clear(); drafts.clear(); draftRevisions.clear(); loaded=null; memoryHost=null;
    render(); updateFooter(); leave(); load();
  });
  else leave();
}
window.addEventListener('message', event => {
  if (embedded && event.origin === location.origin && event.source === parent && event.data?.type === 'chefmate:request-close') requestClose();
});
document.querySelectorAll('a[href="/"]').forEach(link=>{
  link.addEventListener('click',event=>{
    event.preventDefault();
    requestClose();
  });
});
load();
