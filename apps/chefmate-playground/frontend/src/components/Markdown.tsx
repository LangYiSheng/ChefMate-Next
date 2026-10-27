import { memo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { RevealCharacter, revealText, useMarkdownReveal } from './MarkdownReveal';

export function safeLink(raw: string): string | null {
  try {
    const url = new URL(raw);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

const markdownComponents: Components = {
  a: ({ href, children }) => href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
  img: ({ alt }) => <span>{alt || '图片'}</span>,
  span: ({ node, children }) => node?.properties.dataRevealBorn != null
    ? <RevealCharacter key={`${node.properties.dataRevealBorn}:${node.properties.dataRevealOffset}`} born={Number(node.properties.dataRevealBorn)} delay={Number(node.properties.dataRevealDelay)}>{children}</RevealCharacter>
    : <span>{children}</span>,
};

/** Parse CommonMark and GFM as React nodes. Raw model HTML remains text. */
export const Markdown = memo(function Markdown({ text = '', animate = false }: { text?: string; animate?: boolean }) {
  const content = String(text);
  const batches = useMarkdownReveal(content, animate);
  return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={batches.length ? [revealText(batches, content)] : []} urlTransform={url => safeLink(url) || ''}
    components={markdownComponents}>{content}</ReactMarkdown></div>;
});
