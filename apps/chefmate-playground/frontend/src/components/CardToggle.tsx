import { Icon } from './Icon';
import './CardToggle.css';

/** Hides the card body without unmounting its draft, selected tab or pending work. */
export function CardToggle({ title, expanded, controls, onToggle }: { title: string; expanded: boolean; controls: string; onToggle: () => void }) {
  return <button type="button" className="card-disclosure-toggle" aria-label={`${expanded ? '收起' : '展开'}${title}`} title={expanded ? '收起' : '展开'} aria-expanded={expanded} aria-controls={controls} onClick={onToggle}>
    <Icon name={expanded ? 'chevron-up' : 'chevron-down'} />
  </button>;
}
