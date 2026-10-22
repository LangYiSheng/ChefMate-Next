import { node } from './ui-components.js';
import { enhanceControl } from './form-controls.js';
import { navigationIndex } from './settings-navigation.js';

// Accessible controls, grouped tabs and native validation for every settings section.
export function createSettingsFields({currentSection, values, apiKeySet, changed}) {
  const $ = id => document.getElementById(id);
  const selectedTabs = new Map();
  function heading(text) {
    $("settings-form").append(node("h2", "", text));
  }
  function selectSettingsTab(index, focus = false) {
    const buttons = [...$("settings-tabs").querySelectorAll('[role="tab"]')];
    if (!buttons[index]) return;
    selectedTabs.set(currentSection(), index);
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
    selectSettingsTab(Math.min(selectedTabs.get(currentSection()) || 0, tabs.children.length - 1));
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
    const value = values();
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
      input.placeholder = apiKeySet()
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

  return {heading, field, organizeSections, reportSettingsValidity};
}
