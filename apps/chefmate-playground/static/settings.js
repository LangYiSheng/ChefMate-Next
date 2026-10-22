import {createSettingsSections} from './settings-sections.js';
import { node } from "./ui-components.js";
import { applyBranding } from "./branding.js";
import { readAppearance, applyAppearance } from './appearance.js';
import { TimerSound, readKitchenPreferences, saveKitchenPreferences } from './timer-sound.js';
import { icon } from './icons.js';
import {navigationIndex, navigationEdges, revealNavigationItem} from './settings-navigation.js';
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
let memorySaving = false;
let closeWaitMessage = '';
let footerNotice = null;
window.addEventListener('chefmate:memory-busy', event => {
  memorySaving = event.detail?.busy === true;
  updateFooter();
});
window.addEventListener('chefmate:memory-dirty', event => {
  if (event.detail?.dirty === true) dirty.add('memory');
  else if (event.detail?.dirty === false) dirty.delete('memory');
  updateFooter();
});
const sections=createSettingsSections({getState:()=>({current,loaded,status,testing,busy}),names,drafts,
  changed,message,testConnection,updateFooter,reminderSound,api});
function render() { return sections.render(); }
function reportSettingsValidity() { return sections.reportSettingsValidity(); }

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
  return {inline_limit:2,density:'comfortable',...readAppearance()};
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
  footerNotice=text?{text,ok}:null;
  $("settings-message").hidden = !text || ok;
  $("settings-message").className = ok ? "settings-success" : "settings-error";
  $("settings-message").textContent = text;
  updateFooter();
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
  const text = testing
    ? "正在验证原生工具调用…"
    : busy
      ? busyMessage
      : dirty.has(current)
        ? "当前页有未保存修改"
        : footerNotice?.text || (current==='usage'?'按服务端报告统计':current==='about'?'ChefMate 本地工作台':"设置已同步");
  const kind=testing||busy?'busy':dirty.has(current)?'dirty':footerNotice?.ok===false?'error':'saved';
  const status=$("save-state");status.dataset.state=kind;
  status.replaceChildren(icon(({busy:'loader-circle',dirty:'circle-alert',error:'circle-x',saved:'circle-check'})[kind]),node('span','',text));
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
                : section === "interaction" ? {...loaded.interaction}
                : section === "cooking" ? {timer_limit:loaded.cooking?.timer_limit===1?1:3,
                    clear_timer_on_step_change:loaded.cooking?.clear_timer_on_step_change===true,
                    ...readKitchenPreferences()}
                : section === "developer" ? readDeveloper() : {...readDisplay(),reply_suggestions:loaded.interaction?.reply_suggestions!==false,reply_suggestion_count:loaded.interaction?.reply_suggestion_count ?? 3},
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
      if(values.reply_suggestions!==loaded.interaction?.reply_suggestions || values.reply_suggestion_count!==loaded.interaction?.reply_suggestion_count){
        const revision=draftRevisions.get(section);loaded=await api('/api/settings',{method:'PATCH',body:JSON.stringify({revision,section:'interaction',values:{reply_suggestions:values.reply_suggestions,reply_suggestion_count:values.reply_suggestion_count}})});
        for(const [name,baseline] of draftRevisions)if(baseline===revision)draftRevisions.set(name,loaded.revision);
      }
      delete values.reply_suggestions;delete values.reply_suggestion_count;
      values.inline_limit = Number(values.inline_limit);
      localStorage.setItem("chefmate:ui-settings", JSON.stringify(values));
      applyBranding(); applyAppearance(values);
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
      message(section==='cooking'?'计时设置已保存，对当前和新会话立即生效。':section==='display'?'外观与对话偏好已保存。':"设置已保存。接下来的对话使用新设置。", true);
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
  if(sections.refreshInsights())return;
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
    dirty.clear(); drafts.clear(); draftRevisions.clear(); loaded=null; sections.resetMemoryHost();
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
