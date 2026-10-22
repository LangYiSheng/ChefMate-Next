import { node } from './ui-components.js';
import { AppearanceSettings } from './appearance-settings.js';
import { SettingsInsights } from './settings-insights.js';
import { TOOL_NAMES } from './presentation.js';
import { createSettingsFields } from './settings-fields.js';

// Renders section content and owns the lifetime of appearance, insight and memory subviews.
export function createSettingsSections({getState, names, drafts, changed, message, testConnection, updateFooter, reminderSound, api}) {
  const $ = id => document.getElementById(id);
  let memoryHost = null, insights = null, appearanceSettings = null;
  const {heading, field, organizeSections, reportSettingsValidity} = createSettingsFields({
    currentSection:()=>getState().current,
    values:()=>drafts.get(getState().current),
    apiKeySet:()=>getState().loaded.api_key_set,
    changed,
  });
  function render() {
    const {current, loaded, status, testing, busy} = getState();
    insights?.dispose();insights=null;
    appearanceSettings?.dispose();appearanceSettings=null;
    $("settings-form").classList.toggle('has-appearance',current==='display');
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
      heading('配色与字体');
      const mode=field('显示模式', 'theme', {options:[['light','浅色'],['dark','深色'],['system','跟随系统']],
        hint:'跟随系统时，可分别选择白天和夜间配色。'});
      const host=node('section');$('settings-form').append(host);
      appearanceSettings=new AppearanceSettings({host,values:drafts.get(current),onChange:changed});
      mode.addEventListener('change',()=>{appearanceSettings.previewMode=null;appearanceSettings.update();});
      heading("对话显示");
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
      field('显示估算标记','show_estimates',{type:'checkbox',note:'显示用量、营养和上下文 Token 的估算标记。'});
      heading('对话建议');field('显示推荐回复','reply_suggestions',{type:'checkbox',note:'回复后提供接话思路，点击后仍可修改再发送。'});
      field('推荐回复数量','reply_suggestion_count',{type:'number',options:[1,2,3,4,5,6].map(count=>[count,`${count} 条${count===3?'（默认）':''}`])});
    } else if (current === 'memory') {
      const initialize = !memoryHost;
      memoryHost ||= node('section','memory-settings'); memoryHost.id = 'memory-settings';
      $("settings-form").append(memoryHost);
      if (initialize) window.dispatchEvent(new CustomEvent('chefmate:memory-settings',{detail:{container:memoryHost}}));
    } else if (current === "components") {
      const examples={'meal-planning':'5个人，帮我搭配4道家常菜。','pantry-preparation':'这些菜要先准备什么？','cooking-guide':'这一步要计时多久？','recipe-authoring':'把这张菜谱截图整理成我的菜谱。','personal-memory':'记住我不吃香菜。','food-journal':'这是今天的午饭，帮我记一下。'};
      const pageNames={journal:'食光手记',recipes:'菜谱中心',memory:'长期记忆设置'},uiNames={preference_form:'用餐条件',recipe_choices:'菜品选择',recipe_detail:'菜谱详情',meal_plan:'餐单与份量',prep_checklist:'备菜清单',cooking_guide:'烹饪步骤',timers:'计时器',task_progress:'任务进度',food_log:'饮食记录',nutrition_summary:'营养统计'};
      const dependencyNames={retrieval:'菜谱检索',cookbook:'个人菜谱存储',memory:'长期记忆存储',journal:'饮食记录存储'};
      $('settings-form').append(node('p','components-intro','决定小锅搭子能帮你做什么。关闭后保留已有记录。'));
      for (const component of loaded.components || []) {
        const entry=node('section','settings-component'),input=field(component.name,component.id,{type:'checkbox',note:component.description}),row=input.closest('.setting-field');entry.append(row);
        const details=node('details','settings-component-details'),summary=node('summary','','查看能力');details.append(summary);
        const body=node('div','settings-component-body');
        if(examples[component.id]){body.append(node('h3','','可以这样说'),node('p','component-example',examples[component.id]));}
        body.append(node('h3','','可执行功能'));const tools=node('ul','component-tool-list');for(const tool of component.tools || []){const item=node('li');item.append(node('span','',TOOL_NAMES[tool] || tool),node('code','',tool));tools.append(item);}body.append(tools);
        const interfaces=[...(component.ui?.kinds || []).map(key=>uiNames[key] || key),...(component.ui?.pages || []).map(key=>pageNames[key] || key)];
        body.append(node('h3','','界面与组成'),node('p','',interfaces.length?interfaces.join('、'):'在当前对话内使用'));
        const technical=node('dl','component-manifest');for(const [label,value]of [['组件ID',component.id],['指南',component.instructions?.entrypoint || '未提供'],['加载方式',component.instructions?.loading==='on_demand'?'按需读取':component.instructions?.loading || '未提供'],['依赖',(component.dependencies || []).map(key=>dependencyNames[key] || key).join('、') || '无额外依赖'],['状态',component.available===false?'依赖尚未就绪':'已就绪']]){technical.append(node('dt','',label),node('dd','',value));}body.append(technical);details.append(body);entry.append(details);$('settings-form').append(entry);
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

  function refreshInsights() {
    const {current}=getState();
    if(!insights || !['usage','about'].includes(current))return false;
    insights.show(current);return true;
  }
  function resetMemoryHost() { memoryHost=null; }
  return {render, reportSettingsValidity, refreshInsights, resetMemoryHost};
}
