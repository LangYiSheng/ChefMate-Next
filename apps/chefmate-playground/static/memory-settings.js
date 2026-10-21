// Long-term memory has its own revision and controls, separate from model settings.
import {node} from './ui-components.js';
import {enhanceControl} from './form-controls.js';
import {navigationIndex} from './settings-navigation.js';

window.addEventListener('chefmate:memory-settings', async event => {
  const host = event.detail.container;
  let record, draft = '', enabled = true, activeTab = 0, working = false;
  const status = node('p', 'setting-help memory-status', '正在读取记忆…');
  status.setAttribute('role', 'status'); host.append(status);
  async function request(url, options = {}) {
    const response = await fetch(url, {...options, headers:{'Content-Type':'application/json'}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '暂时无法保存，请重试。');
    return data;
  }
  function markDirty() {
    window.dispatchEvent(new CustomEvent('chefmate:memory-dirty', {detail:{dirty:true}}));
  }
  function paint() {
    host.replaceChildren();
    const tabs = node('div', 'settings-tabs memory-tabs'); tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', '长期记忆内容');
    const preferences = node('section', 'memory-pane'), assistant = node('section', 'memory-pane');
    const panels = [preferences, assistant], buttons = [];
    function select(index, focus = false) {
      activeTab = index;
      panels.forEach((panel, item) => {
        panel.hidden = item !== index; buttons[item].setAttribute('aria-selected', String(item === index));
        buttons[item].tabIndex = item === index ? 0 : -1;
      });
      if (focus) buttons[index].focus();
    }
    ['我的偏好', '助手总结'].forEach((text, index) => {
      const button = node('button', 'settings-tab', text); button.type = 'button';
      button.id = `memory-tab-${index}`; button.setAttribute('role', 'tab');
      panels[index].id = `memory-pane-${index}`; panels[index].setAttribute('role', 'tabpanel');
      panels[index].setAttribute('aria-labelledby', button.id); button.setAttribute('aria-controls', panels[index].id);
      button.onclick = () => select(index);
      button.onkeydown = event => {
        const next = navigationIndex(event.key, index, panels.length);
        if (next === null) return;
        event.preventDefault(); select(next, true);
      };
      buttons.push(button); tabs.append(button);
    });
    const row = node('div', 'setting-field setting-switch-field');
    const label = node('label', 'setting-label', '在对话中使用长期记忆');
    const toggle = node('input'); toggle.type = 'checkbox'; toggle.checked = enabled;
    toggle.id = 'personal-memory-enabled'; label.htmlFor = toggle.id;
    label.append(node('small', '', '关闭后，助手不再读取或整理这些记忆。'));
    const control = node('span', 'setting-control'); control.append(enhanceControl(toggle, {label:'长期记忆'}));
    row.append(label, control);
    const title = node('label', 'field-label', '你希望记住的事'); title.htmlFor = 'personal-memory-notes';
    const input = node('textarea'); input.id = 'personal-memory-notes'; input.rows = 7; input.maxLength = 6000;
    input.placeholder = '例如：不吃香菜；喜欢清淡口味；平时两个人吃饭；家里没有烤箱。'; input.value = draft;
    const count = node('span', 'memory-count', `${draft.length} / 6000`); count.id = 'memory-note-count';
    const help = node('p', 'setting-help', '你写下的偏好会优先采用。聊天记录和饮食记录单独保存，助手只在需要时检索相关片段。');
    help.id = 'memory-note-help'; input.setAttribute('aria-describedby', `${help.id} ${count.id}`);
    input.addEventListener('input', () => {draft = input.value; count.textContent = `${draft.length} / 6000`; markDirty();});
    toggle.addEventListener('change', () => {enabled = toggle.checked; markDirty();});
    preferences.append(row, title, enhanceControl(input, {label:'你希望记住的事'}), count, help);
    assistant.append(node('h2', '', '助手整理的记忆'), node('p', 'setting-help', '助手会从你明确表达的长期习惯中整理，方便之后的对话使用。'));
    assistant.append(node('p', 'memory-summary', record.summary || '还没有总结。聊过几次后，你的口味、忌口和做饭习惯会慢慢整理在这里。'));
    const actions = node('div', 'memory-actions');
    const save = node('button', 'primary-button', '保存记忆'); save.type = 'button';
    const reload = node('button', 'text-button', '读取最新'); reload.type = 'button';
    const clear = node('button', 'text-button memory-clear', '清空记忆'); clear.type = 'button';
    const right = node('div', 'memory-primary-actions'); right.append(reload, save); actions.append(clear, right);
    const confirmation = node('div', 'memory-clear-confirm'); confirmation.hidden = true;
    const yes = node('button', 'secondary-button', '确认清空'); yes.type = 'button';
    const no = node('button', 'text-button', '保留记忆'); no.type = 'button';
    const decision = node('div', 'memory-confirm-actions'); decision.append(no, yes);
    confirmation.append(node('p', '', '会清空你写的偏好和助手总结，历史对话和饮食记录仍保留。'), decision);
    clear.onclick = () => {confirmation.hidden = false; yes.focus();};
    no.onclick = () => {confirmation.hidden = true; clear.focus();};
    async function run(operation, success = '记忆已保存。') {
      if (working) return;
      working = true;
      window.dispatchEvent(new CustomEvent('chefmate:memory-busy', {detail:{busy:true}}));
      for (const button of [save, reload, clear, yes, no]) button.disabled = true;
      input.disabled = toggle.disabled = true;
      input._syncControl?.(); toggle._syncControl?.();
      status.textContent = '正在处理…';
      let refreshed = false, resultMessage = success;
      try {
        record = await operation(); draft = record.user_notes; enabled = record.enabled;
        window.dispatchEvent(new CustomEvent('chefmate:memory-dirty', {detail:{dirty:false}}));
        refreshed = true;
      } catch (error) {resultMessage = error.message;}
      finally {
        working = false;
        window.dispatchEvent(new CustomEvent('chefmate:memory-busy', {detail:{busy:false}}));
        for (const button of [save, reload, clear, yes, no]) button.disabled = false;
        input.disabled = toggle.disabled = false;
        input._syncControl?.(); toggle._syncControl?.();
        if (refreshed) paint();
        status.textContent = resultMessage;
      }
    }
    save.onclick = () => run(() => request('/api/memory', {method:'PATCH', body:JSON.stringify({revision:record.revision, user_notes:draft, enabled})}));
    reload.onclick = () => {
      if (draft !== record.user_notes || enabled !== record.enabled) {
        status.textContent = '当前还有未保存修改，请先保存记忆。'; return;
      }
      run(() => request('/api/memory'), '已读取最新记忆。');
    };
    yes.onclick = () => run(() => request(`/api/memory?revision=${record.revision}`, {method:'DELETE'}), '记忆已清空。');
    host.append(tabs, preferences, assistant, confirmation, actions, status);
    select(activeTab);
    status.textContent = '记忆保存在本机，可随时修改。';
  }
  try {record = await request('/api/memory'); draft = record.user_notes; enabled = record.enabled; paint();}
  catch (error) {status.textContent = error.message;}
});
