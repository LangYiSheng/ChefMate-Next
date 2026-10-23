import {useEffect, useRef, useState, type RefObject} from 'react';

type IndexItem = {id: string; title: string; target: HTMLElement};
export function SettingsIndex({container, section}: {container: RefObject<HTMLDivElement | null>; section: string}) {
  const targets = useRef<IndexItem[]>([]), [items, setItems] = useState<{id: string; title: string}[]>([]), [active, setActive] = useState('');
  useEffect(() => {
    const root = container.current, document = root?.querySelector<HTMLElement>('[data-settings-document]');
    if (!root || !document) return;
    const offset = () => ([...document.querySelectorAll<HTMLElement>('.settings-tabs')].find(element => !element.closest('[hidden]'))?.getBoundingClientRect().height || 0) + 16;
    const locate = () => {
      const boundary = root.getBoundingClientRect().top + offset() + 20;
      const current = targets.current.findLast(item => item.target.getBoundingClientRect().top <= boundary) || targets.current[0];
      setActive(current?.id || '');
    };
    const collect = () => {
      targets.current = [...document.querySelectorAll<HTMLElement>('[data-settings-index][id]')].filter(element => !element.closest('[hidden]')).map(target => ({id: target.id, title: target.dataset.settingsIndex || '', target}));
      const next = targets.current.map(({id, title}) => ({id, title}));
      setItems(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next); locate();
    };
    collect();
    const observer = new MutationObserver(collect), resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(locate);
    observer.observe(document, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'open']});
    resize?.observe(document); resize?.observe(root);
    root.addEventListener('scroll', locate, {passive: true}); window.addEventListener('resize', locate);
    return () => {observer.disconnect(); resize?.disconnect(); root.removeEventListener('scroll', locate); window.removeEventListener('resize', locate); targets.current = [];};
  }, [container, section]);
  if (!['memory', 'components', 'changelog', 'usage', 'about', 'data'].includes(section) || items.length < 2) return null;
  return <aside className="settings-index"><nav aria-label="当前设置目录"><span>本页内容</span><ol>{items.map(item => <li key={item.id}><button type="button" aria-current={active === item.id ? 'location' : undefined} onClick={() => {
    const root = container.current, target = targets.current.find(candidate => candidate.id === item.id)?.target;
    if (!root || !target) return;
    const tabsHeight = [...root.querySelectorAll<HTMLElement>('[data-settings-document] .settings-tabs')].find(element => !element.closest('[hidden]'))?.getBoundingClientRect().height || 0;
    const top = Math.max(0, root.scrollTop + target.getBoundingClientRect().top - root.getBoundingClientRect().top - tabsHeight - 16);
    root.scrollTo({top, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'}); setActive(item.id);
  }}>{item.title}</button></li>)}</ol></nav></aside>;
}
