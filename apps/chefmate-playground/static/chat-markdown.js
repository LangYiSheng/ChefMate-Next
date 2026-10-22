import { node as element } from './ui-components.js';

// A deliberately small, text-node renderer; model output never becomes HTML.
export function safeLink(raw) {
  try {
    const url = new URL(raw);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function inlineMarkdown(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    if (token.startsWith('`')) parent.append(element('code', '', token.slice(1, -1)));
    else if (token.startsWith('**')) parent.append(element('strong', '', token.slice(2, -2)));
    else {
      const parts = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = safeLink(parts[2]);
      const link = element(href ? 'a' : 'span', '', parts[1]);
      if (href) { link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      parent.append(link);
    }
    last = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(last)));
}

export function markdown(text) {
  const fragment = document.createDocumentFragment();
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^\s*```/.test(line)) {
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      if (i < lines.length) i++;
      const pre = element('pre');
      pre.append(element('code', '', code.join('\n')));
      fragment.append(pre);
      continue;
    }
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || '')) {
      const split = (row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
      const table = element('table');
      const head = element('thead');
      const header = element('tr');
      for (const cell of split(line)) { const th = element('th'); inlineMarkdown(th, cell); header.append(th); }
      head.append(header); table.append(head);
      const body = element('tbody');
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        const row = element('tr');
        for (const cell of split(lines[i++])) { const td = element('td'); inlineMarkdown(td, cell); row.append(td); }
        body.append(row);
      }
      table.append(body);
      const wrapper = element('div', 'table-scroll'); wrapper.append(table); fragment.append(wrapper);
      continue;
    }
    const heading = line.match(/^\s*#{1,6}\s+(.+)$/);
    if (heading) { const h = element('h3'); inlineMarkdown(h, heading[1]); fragment.append(h); i++; continue; }
    const listMatch = line.match(/^\s*(?:([-*])|(\d+)[.)])\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[2]);
      const list = element(ordered ? 'ol' : 'ul');
      if (ordered) list.start = Number(listMatch[2]);
      while (i < lines.length) {
        const item = lines[i].match(/^\s*(?:([-*])|(\d+)[.)])\s+(.+)$/);
        if (!item || Boolean(item[2]) !== ordered) break;
        const li = element('li'); inlineMarkdown(li, item[3]); list.append(li); i++;
      }
      fragment.append(list); continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = element('blockquote'); inlineMarkdown(quote, line.replace(/^\s*>\s?/, '')); fragment.append(quote); i++; continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { fragment.append(element('hr')); i++; continue; }
    const paragraph = element('p');
    inlineMarkdown(paragraph, line);
    i++;
    while (i < lines.length && lines[i].trim() && !/^\s*(?:#{1,6}\s|[-*]\s|\d+[.)]\s|```|>)/.test(lines[i])
      && !(lines[i].includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || ''))) {
      paragraph.append(element('br'));
      inlineMarkdown(paragraph, lines[i++]);
    }
    fragment.append(paragraph);
  }
  return fragment;
}
