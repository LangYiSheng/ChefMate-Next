import { node } from "./ui-components.js";
import { BRAND_ICONS, iconPath, applyBranding } from "./branding.js";
import { icon } from './icons.js';
applyBranding();
const embedded = window.parent !== window && new URLSearchParams(location.search).get('embedded') === '1';
document.body.classList.toggle('settings-embedded', embedded);
document.querySelectorAll('[data-icon]').forEach(node => node.replaceChildren(icon(node.dataset.icon)));
function notifyParent(type) { if (embedded) window.parent.postMessage({ type }, location.origin); }

const $ = (id) => document.getElementById(id);
const names = {
  model: ["模型连接", "选择模型、端点和连接方式。保存后用于接下来的对话。"],
  display: ["界面显示", "把注意力留给菜谱和下一步。"],
  interaction: ["对话建议", "让下一步更好开口，也保留你自己的表达。"],
  developer: ["开发者", "按需查看公开执行记录、工具参数和模型实际用量。"],
  skills: ["厨房技能", "让助手按需使用你启用的厨房知识。"],
  runtime: ["执行设置", "设置一次任务的等待时间和操作上限。"],
  context: ["上下文预算", "给查询结果和连续对话留出足够空间。"],
  data: ["数据与状态", "查看当前菜谱库和本地工作台状态。"],
};
const skillLabels = {
  "meal-planning": ["选菜与餐单", "按食材、时间和人数安排这一餐。"],
  "pantry-preparation": [
    "备菜与准备",
    "核对已有和缺少的食材，逐项确认准备情况。",
  ],
  "cooking-guide": ["烹饪引导", "跟随原始步骤、确认完成情况和管理计时。"],
};
let current = "model",
  loaded = null,
  status = null,
  busy = false,
  testing = false;
const drafts = new Map(),
  draftRevisions = new Map(),
  dirty = new Set();
function readDisplay() {
  try {
    const { inline_limit = 2, density = "comfortable", brand_icon = "pot-c" } = JSON.parse(localStorage.getItem("chefmate:ui-settings") || "{}");
    return { inline_limit, density, brand_icon };
  } catch {
    return { inline_limit: 2, density: "comfortable", brand_icon: "pot-c" };
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
  const readOnly = current === "data";
  $("save-settings").hidden = readOnly;
  $("reload-settings").disabled = busy || testing;
  $("save-settings").disabled =
    !loaded || !dirty.has(current) || busy || testing;
  $("save-state").textContent = testing
    ? "正在验证原生工具调用…"
    : busy
      ? "正在保存…"
      : dirty.has(current)
        ? "当前页有未保存修改"
        : "设置已同步";
  if ($("test-model")) $("test-model").disabled = busy || testing;
  document.querySelectorAll("[data-section]").forEach((button) => {
    button.setAttribute(
      "aria-current",
      button.dataset.section === current ? "page" : "false",
    );
  });
}
function heading(text) {
  $("settings-form").append(node("h2", "", text));
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
  const row = node("label", "setting-field"),
    copy = node("span", "setting-label", label),
    control = node("span", "setting-control");
  if (note) copy.append(node("small", "", note));
  const input = node(options ? "select" : "input");
  input.name = key;
  input.id = `setting-${key}`;
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
  input.addEventListener(
    options || type === "checkbox" ? "change" : "input",
    () => {
      value[key] =
        type === "checkbox"
          ? input.checked
          : type === "number"
            ? Number(input.value)
            : input.value;
      changed();
    },
  );
  control.append(input);
  if (hint) control.append(node("small", "", hint));
  row.append(copy, control);
  $("settings-form").append(row);
  return input;
}
function render() {
  document.querySelector('.settings-content').scrollTop = 0;
  $("section-title").textContent = names[current][0];
  $("section-description").textContent = names[current][1];
  $("settings-form").replaceChildren();
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
      hint: "密钥仅保存在本机，不回显，也不写进聊天记录。",
    });
    heading("连接方式");
    field("支持图片输入", "supports_images", {type:"checkbox",note:"模型能看图片时开启；聊天输入框会出现上传图片入口。",hint:"照片会自动压缩，可发送食材、包装标签或做菜现场。不支持图片的模型请关闭。"});
    field("同时接收的图片上限", "max_images", {type:"number", min:1, max:12,
      note:"按模型实际支持的数量设置，默认 3 张。",
      hint:"每次请求共 1–12 张，包含历史图片。超出时会移除最早的完整历史轮次，聊天记录仍保留。"});
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
  } else if (current === "developer") {
    heading("调试信息");
    field("开发者模式", "enabled", { type: "checkbox", note: "默认关闭。在右侧调试面板查看可用工具、调用记录、技能与实际上下文。" });
    field("记录展示方式", "trace_mode", {
      options: [
        ["compact", "收起记录 · 按需展开"],
        ["detailed", "展开公开执行轨迹"],
      ],
      note: "只展示公开操作与工具状态。",
    });
    $("settings-form").append(node("p", "settings-note", "用量来自模型服务实际返回，未返回时显示“未报告”。上下文调试保留最近20轮的脱敏实际请求；图片只留元数据，不展示内部推理。这个开关控制当前浏览器中的展示。"));
  } else if (current === "display") {
    heading("ChefMate 图标");
    const candidates=node("div","brand-candidates");
    for(const icon of BRAND_ICONS) {
      const option=node("label","brand-candidate"),image=node("img"),radio=node("input");
      image.src=iconPath(icon.id);image.alt=icon.name;radio.type="radio";radio.name="brand-icon";radio.value=icon.id;
      radio.checked=drafts.get(current).brand_icon===icon.id;
      radio.addEventListener("change",()=>{drafts.get("display").brand_icon=icon.id;changed();});
      option.append(image,node("strong","",icon.name),node("small","",icon.note),radio);candidates.append(option);
    }
    $("settings-form").append(candidates);
    heading("核心内容");
    field("每次回复展开几项内容", "inline_limit", {
      options: [
        [1, "只展开一项，例如食材清单"],
        [2, "最多两项，例如餐单和食材清单"],
      ],
      note: "控制聊天中展开的交互内容；更多内容仍可在工作区打开。",
      hint: "助手决定展示哪些内容，这里只控制同时展开的数量，不限制查询数量或工具调用。",
    });
    field("内容间距", "density", {
      options: [
        ["comfortable", "宽松 · 内容之间留白更多"],
        ["compact", "紧凑 · 同一屏显示更多内容"],
      ],
      hint:"只改变间距，不会省略食材、步骤或改变模型回答。全屏做菜模式会自动使用更大的文字。",
    });
    $("settings-form").append(
      node(
        "p",
        "settings-note",
        "表单、选菜和清单可以在聊天中操作。补充资料放在工作区，已有内容随任务更新。",
      ),
    );
  } else if (current === "skills") {
    heading("启用的技能");
    for (const [name, [label, note]] of Object.entries(skillLabels))
      field(label, name, { type: "checkbox", note });
    $("settings-form").append(
      node(
        "p",
        "settings-note",
        "启用后，助手会按需读取技能指引。勾选技能不会直接执行厨房操作。",
      ),
    );
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
        "会话、厨房进度和计时器随当前任务保存。模型与技能设置保存在本机，界面显示偏好只作用于这个浏览器。",
      ),
    );
  }
  updateFooter();
}
async function load() {
  busy = true;
  updateFooter();
  try {
    const result = await Promise.all([
      api("/api/settings"),
      api("/api/status"),
    ]);
    loaded = result[0];
    status = result[1];
    for (const section of ["model", "skills", "runtime", "context", "interaction", "display", "developer"])
      if (!dirty.has(section)) {
        draftRevisions.set(section,loaded.revision);
        drafts.set(
          section,
          section === "model"
            ? { ...loaded.model, api_key: "" }
            : section === "skills"
              ? Object.fromEntries(
                  loaded.available_skills.map((name) => [
                    name,
                    loaded.enabled_skills.includes(name),
                  ]),
                )
              : section === "runtime"
                ? { ...loaded.runtime }
                : section === "context"
                  ? { ...loaded.context }
                : section === "interaction" ? {reply_suggestions:loaded.interaction?.reply_suggestions !== false}
                : section === "developer" ? readDeveloper() : readDisplay(),
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
  if (busy || testing || !$("settings-form").reportValidity()) return;
  const section = current,
    values = { ...drafts.get(section) };
  busy = true;
  updateFooter();
  message("");
  try {
    if (section === "developer") {
      localStorage.setItem("chefmate:developer-settings", JSON.stringify(values));
    } else if (section === "display") {
      values.inline_limit = Number(values.inline_limit);
      localStorage.setItem("chefmate:ui-settings", JSON.stringify(values));
      applyBranding(values.brand_icon);
    } else {
      const payload =
        section === "skills"
          ? {
              enabled_skills: Object.keys(values).filter(
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
    }
    dirty.delete(section);
    notifyParent('chefmate:settings-saved');
    if (current === section) {
      render();
      message("设置已保存。接下来的对话使用新设置。", true);
    }
  } catch (error) {
    message(error.message);
  } finally {
    busy = false;
    updateFooter();
  }
}
async function testConnection() {
  if (testing || !$("settings-form").reportValidity()) return;
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
  const section=current;
  const reload=()=>{
    dirty.delete(section);
    load();
  };
  if(dirty.has(section)) confirmInPage("放弃当前页未保存的修改，读取最新配置？其他分类的草稿会保留。",reload);
  else reload();
});
function requestClose() {
  const leave = () => embedded ? notifyParent('chefmate:close-settings') : location.assign('/');
  if (dirty.size) confirmInPage("返回对话会放弃尚未保存的设置。可以先保存，或者继续返回。", () => {
    dirty.clear(); drafts.clear(); draftRevisions.clear(); loaded=null;
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
