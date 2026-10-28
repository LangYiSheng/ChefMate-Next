import {useEffect, type RefObject} from 'react';

/** Replay the page entrance without remounting forms or losing their drafts. */
export function usePageArrival(target: RefObject<HTMLElement | null>, page: string) {
  useEffect(() => {
    const node = target.current, media = window.matchMedia('(prefers-reduced-motion: reduce)');
    if (!node?.animate || media.matches) return;
    const style = getComputedStyle(node), timing = style.getPropertyValue('--motion-slow').trim();
    const duration = timing.endsWith('ms') ? parseFloat(timing) : parseFloat(timing) * 1000;
    const animation = node.animate([{opacity: 0, transform: 'translateY(7px)'}, {opacity: 1, transform: 'translateY(0)'}], {
      duration: Number.isFinite(duration) ? duration : 380,
      easing: style.getPropertyValue('--ease-settle').trim() || 'cubic-bezier(.22,1,.36,1)',
    });
    const reduce = () => {if (media.matches) animation.cancel();};
    media.addEventListener('change', reduce);
    return () => {animation.cancel(); media.removeEventListener('change', reduce);};
  }, [page, target]);
}
