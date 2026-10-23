import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function safeLink(raw: string): string | null {
  try {
    const url = new URL(raw);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

/** Parse CommonMark and GFM as React nodes. Raw model HTML remains text. */
export function Markdown({ text = '' }: { text?: string }) {
  return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={url => safeLink(url) || ''}
    components={{
      a: ({ href, children }) => href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
      img: ({ alt }) => <span>{alt || '图片'}</span>,
    }}>{String(text)}</ReactMarkdown></div>;
}
