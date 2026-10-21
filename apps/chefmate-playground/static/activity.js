import {icon} from './icons.js';
import {ActivityTimeline, activityFrame} from './activity-state.js';

const timeline = new ActivityTimeline();
export function activityLabel(text, key = 'current') {
  const label = document.createElement('span');
  label.className = 'activity-label';
  const typed = document.createElement('span');
  typed.className = 'activity-text';
  typed.setAttribute('aria-hidden','true');
  const accessible = document.createElement('span');
  accessible.className = 'visually-hidden';
  accessible.textContent = text;
  label.append(icon('loader-circle','activity-spinner'),typed,accessible);
  const start = timeline.start(key,text,performance.now());
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const draw = () => {
    const frame = activityFrame(text,performance.now()-start,reduced);
    typed.textContent = frame.text;
    return frame.done;
  };
  if (!draw()) requestAnimationFrame(function tick() {
    if (label.isConnected && !draw()) requestAnimationFrame(tick);
  });
  return label;
}
