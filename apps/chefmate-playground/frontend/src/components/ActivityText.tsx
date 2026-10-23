import { useEffect, useState } from 'react';
import { Icon } from './Icon';

export function activityFrame(text: string, elapsed: number, reduced = false) {
  const characters = Array.from(text), duration = Math.min(1800, characters.length * 72);
  const count = reduced || !duration ? characters.length : Math.min(characters.length, Math.max(1, Math.ceil(characters.length * Math.max(0, elapsed) / duration)));
  return characters.slice(0, count).join('');
}

/** The accessible status changes once; intermediate typing is purely visual. */
export function ActivityText({ text, active = true }: { text: string; active?: boolean }) {
  const [frame, setFrame] = useState({ text, value: activityFrame(text, 0) });
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)'), start = performance.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const draw = () => {
      clearTimeout(timer);
      const value = activityFrame(text, performance.now() - start, media.matches || !active);
      setFrame(previous => previous.text === text && previous.value === value ? previous : { text, value });
      if (value !== text) timer = setTimeout(draw, 36);
    };
    draw(); media.addEventListener('change', draw);
    return () => { clearTimeout(timer); media.removeEventListener('change', draw); };
  }, [text, active]);
  return <div className="assistant-progress" role="status"><Icon name="loader-circle" className="spin" /><span aria-hidden="true" className="activity-text">{frame.text === text ? frame.value : activityFrame(text, 0)}</span><span className="visually-hidden">{text}</span></div>;
}
