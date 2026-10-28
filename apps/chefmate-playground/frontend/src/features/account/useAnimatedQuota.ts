import {useEffect, useRef, useState} from 'react';

const reducedMotion = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
/** Animate the first reveal from zero; later settlements move from the visible value. */
export function useAnimatedQuota(target: number | null) {
  const [value, setValue] = useState<number | null>(() => reducedMotion() ? target : target == null ? null : 0);
  const current = useRef(value);
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0, start: number | undefined;
    const from = current.current ?? 0;
    const update = (next: number | null) => {current.current = next; setValue(next);};
    const finish = () => {cancelAnimationFrame(frame); update(target);};
    const changed = () => {if (media.matches) finish();};
    media.addEventListener('change', changed);
    if (target == null || media.matches || target === from) finish();
    else {
      const tick = (now: number) => {
        start ??= now;
        const progress = Math.min(1, Math.max(0, (now - start) / 720));
        update(from + (target - from) * (1 - (1 - progress) ** 3));
        if (progress < 1) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    }
    return () => {cancelAnimationFrame(frame); media.removeEventListener('change', changed);};
  }, [target]);
  return value;
}
