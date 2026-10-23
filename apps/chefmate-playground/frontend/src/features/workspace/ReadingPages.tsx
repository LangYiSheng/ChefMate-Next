import { useLayoutEffect, useRef, useState } from 'react';

/** Keep every character, including whitespace and supplementary Unicode. */
export function splitReadingText(text: string, capacity: number): string[] {
  if (!text) return [''];
  const characters = Array.from(text), pages: string[] = [];
  let page = '', used = 0;
  for (const character of characters) {
    const width = /[\u0000-\u007f]/.test(character) ? .58 : 1;
    if (used + width > Math.max(40, capacity) && page) { pages.push(page); page = ''; used = 0; }
    page += character; used += character === '\n' ? 8 : width;
  }
  if (page) pages.push(page);
  return pages;
}

export function ReadingPages({ text, label = '阅读内容' }: { text: string; label?: string }) {
  const viewport = useRef<HTMLDivElement>(null), [capacity, setCapacity] = useState(650), [anchor, setAnchor] = useState(0);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      const style = getComputedStyle(element), font = parseFloat(style.fontSize) || 18, line = parseFloat(style.lineHeight) || font * 1.8;
      setCapacity(Math.max(60, Math.floor(rect.width / font) * Math.max(3, Math.floor(rect.height / line) - 1)));
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element); window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, []);
  const pages = splitReadingText(text, capacity);
  let offset = 0, page = 0;
  for (let index = 0; index < pages.length; index++) { if (offset <= anchor) page = index; offset += pages[index].length; }
  const go = (next: number) => { setAnchor(pages.slice(0, next).join('').length); if (viewport.current) viewport.current.scrollTop = 0; };
  return <div className="workspace-reading-pages">
    <div className="workspace-reading-viewport" ref={viewport}><p className="ui-step-instruction">{pages[page]}</p></div>
    {pages.length > 1 && <nav className="page-controls reading-pagination" aria-label={`${label}分页`}><button className="page-button" type="button" disabled={!page} onClick={() => go(page - 1)}>上一页</button><span>{label} {page + 1} / {pages.length}</span><button className="page-button" type="button" disabled={page === pages.length - 1} onClick={() => go(page + 1)}>下一页</button></nav>}
  </div>;
}
