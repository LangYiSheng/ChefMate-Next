import { node } from "./ui-components.js";

const $ = (id) => document.getElementById(id);
const names = {
  model: ["模型连接", "选择模型、端点和连接方式。保存后用于接下来的对话。"],
  display: ["界面显示", "把注意力留给菜谱和下一步。"],
  skills: ["厨房技能", "让助手按需使用你启用的厨房知识。"],
  runtime: ["执行设置", "设置一次任务的等待时间和操作上限。"],
  data: ["数据与状态", "查看当前菜谱库和本地工作台状态。"],
};
const skillLabels = {
  "meal-planning": ["选菜与餐单", "按食材、时间和人数安排这一餐。"],
  "pantry-preparation": [
    "采购与备菜",
    "核对食材、整理缺料，逐项确认准备情况。",
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
    return {
      inline_limit: 2,
      density: "comfortable",
      ...JSON.parse(localStorage.getItem("chefmate:ui-settings") || "{}"),
      trace_mode: localStorage.getItem("chefmate:trace-mode") || "compact",
    };
  } catch {
    return { trace_mode: "compact", inline_limit: 2, density: "comfortable" };
  }
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
  $("section-title").textContent = names[current][0];
  $("section-description").textContent = names[current][1];
  $("settings-form").replaceChildren();
  message("");
  if (!loaded) {
    $("settings-form").append(node("p", "settings-note", "正在读取配置…"));
    return;
  }
  if (current === "model") {
    const tip = node("div", "protocol-tip");
    tip.append(node("p", "", "测试配置：apiclaude · gpt-5.6-luna"));
    const preset = node("button", "secondary-button", "填入测试配置");
    preset.type = "button";
    preset.addEventListener("click", () => {
      drafts.set("model", {
        ...drafts.get("model"),
        base_url: "https://apiclaude.cc/v1",
        model: "gpt-5.6-luna",
        api_key: "",
        api_format: "chat_completions",
        stream_tool_calls: false,
      });
      dirty.add("model");
      render();
      message("已填入端点和模型，请填写 apiclaude 的专用密钥。", true);
    });
    tip.append(preset);
    $("settings-form").append(tip);
    heading("连接信息");
    field("API 端点", "base_url", { hint: "OpenAI 兼容端点通常以 /v1 结尾。" });
    field("模型名称", "model");
    field("API Key", "api_key", {
      type: "password",
      hint: "密钥仅保存在本机，不回显，也不写进聊天记录。",
    });
    heading("连接方式");
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
  } else if (current === "display") {
    heading("执行过程");
    field("展示方式", "trace_mode", {
      options: [
        ["compact", "单行摘要 · 完成后收起"],
        ["detailed", "展开公开执行轨迹"],
        ["hidden", "隐藏执行过程"],
      ],
      note: "只展示公开操作与工具状态。",
    });
    const preview = node("div", "display-preview", "◌ 正在查找适合的虾菜谱…");
    preview.append(node("div", "", "✓ 找到候选，等待你选菜"));
    $("settings-form").append(preview);
    heading("核心内容");
    field("每轮交互内容上限", "inline_limit", {
      options: [
        [1, "一项 · 更专注"],
        [2, "两项 · 默认"],
      ],
      note: "具体展示什么由助手决定。",
    });
    field("信息密度", "density", {
      options: [
        ["comfortable", "舒适"],
        ["compact", "紧凑"],
      ],
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
    for (const section of ["model", "skills", "runtime", "display"])
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
                : readDisplay(),
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
    if (section === "display") {
      values.inline_limit = Number(values.inline_limit);
      localStorage.setItem("chefmate:trace-mode", values.trace_mode);
      localStorage.setItem("chefmate:ui-settings", JSON.stringify(values));
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
});
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
document.querySelectorAll('a[href="/"]').forEach(link=>{
  link.addEventListener('click',event=>{
    if(!dirty.size)return;
    event.preventDefault();
    confirmInPage("返回对话会放弃尚未保存的设置。可以先保存，或者继续返回。",()=>location.assign('/'));
  });
});
load();
