import { Fragment, type ReactNode } from 'react';

export function safeLink(raw: string): string | null {
  try {
    const url = new URL(raw);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g)) {
    nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith('`')) nodes.push(<code key={match.index}>{token.slice(1, -1)}</code>);
    else if (token.startsWith('**')) nodes.push(<strong key={match.index}>{token.slice(2, -2)}</strong>);
    else {
      const parts = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/)!;
      const href = safeLink(parts[2]);
      nodes.push(href ? <a key={match.index} href={href} target="_blank" rel="noopener noreferrer">{parts[1]}</a> : <span key={match.index}>{parts[1]}</span>);
    }
    last = match.index! + token.length;
  }
  nodes.push(text.slice(last));
  return nodes;
}

/** Model output is always React text; arbitrary HTML is never executed. */
export function Markdown({ text = '' }: { text?: string }) {
  const lines = String(text).split('\n');
  const blocks: ReactNode[] = [];
  for (let i = 0; i < lines.length; i++) {
    const value = lines[i];
    if (!value.trim()) continue;
    if (value.startsWith('```')) {
      const key = i;
      const code: string[] = [];
      while (++i < lines.length && !lines[i].startsWith('```')) code.push(lines[i]);
      blocks.push(<pre key={key}><code>{code.join('\n')}</code></pre>);
    } else if (/^#{1,4}\s/.test(value)) blocks.push(<h3 key={i}>{inline(value.replace(/^#+\s/, ''))}</h3>);
    else if (/^\s*(?:[-*]|\d+[.)])\s/.test(value)) {
      const key = i;
      const ordered = /^\s*\d/.test(value);
      const rows: ReactNode[] = [];
      while (i < lines.length && (ordered ? /^\s*\d+[.)]\s/ : /^\s*[-*]\s/).test(lines[i])) {
        rows.push(<li key={i}>{inline(lines[i].replace(/^\s*(?:[-*]|\d+[.)])\s/, ''))}</li>);
        i++;
      }
      i--;
      blocks.push(ordered ? <ol key={key}>{rows}</ol> : <ul key={key}>{rows}</ul>);
    } else blocks.push(<p key={i}>{inline(value)}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
