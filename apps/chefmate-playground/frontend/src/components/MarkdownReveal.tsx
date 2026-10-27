import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import type { Root, RootContent } from 'hast';
import './MarkdownReveal.css';

const duration = 360;
const maximumDelay = 420;
const characterBudget = 600;
const lifetime = duration + maximumDelay + 30;
const graphemes = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
type Batch = { start: number; end: number; born: number; delays: Map<number, number> };
type Reveal = { text: string; batches: Batch[] };

/** Track arrival, not renders. Existing/history text is never queued for playback. */
export function useMarkdownReveal(text: string, animate: boolean) {
  const [reduced, setReduced] = useState(() => animate && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const [reveal, setReveal] = useState<Reveal>(() => ({ text, batches: [] }));
  const previousText = useRef(text);
  useEffect(() => {
    if (!animate) return;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const changed = () => setReduced(media.matches);
    changed(); media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  }, [animate]);
  useLayoutEffect(() => {
    const before = previousText.current;
    previousText.current = text;
    if (!animate || reduced) {
      if (reveal.batches.length) setReveal(previous => previous.batches.length ? { text, batches: [] } : previous);
      return;
    }
    if (before === text) return;
    setReveal(previous => {
      let prefix = 0;
      while (prefix < before.length && prefix < text.length && before[prefix] === text[prefix]) prefix++;
      // A rewritten answer starts a fresh suffix; removed text must not retain animation state.
      const now = performance.now();
      const characters = [...graphemes.segment(text)];
      const budgetStart = characters[Math.max(0, characters.length - characterBudget)]?.index ?? 0;
      const batches = previous.batches.filter(batch => batch.end <= prefix && batch.end > budgetStart && now - batch.born < lifetime)
        .map(batch => ({ ...batch, start: Math.max(batch.start, budgetStart) }));
      if (prefix < text.length) {
        const start = Math.max(prefix, budgetStart);
        const arrived = characters.filter(character => character.index >= start);
        const step = Math.min(12, maximumDelay / Math.max(1, arrived.length - 1));
        batches.push({ start, end: text.length, born: now,
          delays: new Map(arrived.map((character, index) => [character.index, index * step])),
        });
      }
      return { text, batches };
    });
  }, [text, animate, reduced]);
  useEffect(() => {
    if (!reveal.batches.length) return;
    const expiry = Math.min(...reveal.batches.map(batch => batch.born + lifetime));
    const timer = setTimeout(() => setReveal(previous => ({ ...previous,
      batches: previous.batches.filter(batch => performance.now() - batch.born < lifetime),
    })), Math.max(1, expiry - performance.now()));
    return () => clearTimeout(timer);
  }, [reveal]);
  // Retain existing leaves during the render preceding the layout update, so appends do not remount them.
  return animate && !reduced ? reveal.batches : [];
}

/** Decorate parsed text leaves, keeping links, lists and GFM structure intact. */
export function revealText(batches: Batch[], source: string) {
  return () => (tree: Root) => {
    const visit = (parent: Root | Extract<RootContent, { type: 'element' }>, code = false) => {
      const output: RootContent[] = [];
      const plain = (value: string) => {
        const last = output.at(-1);
        if (last?.type === 'text') last.value += value;
        else output.push({ type: 'text', value });
      };
      for (const child of parent.children) {
        if (child.type === 'element') {
          visit(child, code || child.tagName === 'code' || child.tagName === 'pre');
          output.push(child);
        } else if (child.type !== 'text' || code || child.position?.start.offset == null) output.push(child);
        else {
          const start = child.position.start.offset;
          const end = child.position.end.offset ?? start + child.value.length;
          const applicable = batches.filter(batch => batch.start < end && batch.end > start);
          if (!applicable.length) { output.push(child); continue; }
          const raw = source.slice(start, end);
          let cursor = 0, reference = 0;
          for (const { segment } of graphemes.segment(child.value)) {
            let offset = start + cursor;
            const entity = raw.slice(cursor).match(/^&(?:#x[\da-f]+|#\d+|[a-z][a-z\d]+);?/i)?.[0];
            if (entity) { reference = offset; cursor += entity.length; }
            else if (raw.slice(cursor, cursor + segment.length) === segment) cursor += segment.length;
            else if (raw[cursor] === '\\' && raw.slice(cursor + 1, cursor + 1 + segment.length) === segment) cursor += segment.length + 1;
            else if (segment === '\n' && raw.slice(cursor, cursor + 2) === '\r\n') cursor += 2;
            else offset = reference;
            const batch = applicable.find(item => item.start <= offset && item.end > offset);
            if (!batch || /^\s+$/u.test(segment)) plain(segment);
            else output.push({ type: 'element', tagName: 'span', properties: {
              className: ['stream-character'], dataRevealBorn: batch.born,
              dataRevealDelay: batch.delays.get(offset) ?? 0, dataRevealOffset: offset,
            }, children: [{ type: 'text', value: segment }] });
          }
        }
      }
      parent.children = output;
    };
    visit(tree);
  };
}

export function RevealCharacter({ born, delay, children }: { born: number; delay: number; children: ReactNode }) {
  // A Markdown rewrite may remount a leaf. Resume its original timeline instead of replaying it.
  const [offset] = useState(() => delay - Math.max(0, performance.now() - born));
  return <span className="stream-character" style={{ '--character-delay': `${offset}ms` } as CSSProperties}>{children}</span>;
}
