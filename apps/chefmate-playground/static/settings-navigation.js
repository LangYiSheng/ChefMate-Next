export function navigationIndex(key, current, length) {
  if (!length) return null;
  if (key === 'Home') return 0;
  if (key === 'End') return length - 1;
  if (key === 'ArrowRight') return (Math.max(0, current) + 1) % length;
  if (key === 'ArrowLeft') return (Math.max(0, current) - 1 + length) % length;
  return null;
}

export function navigationEdges(scrollLeft, clientWidth, scrollWidth) {
  const maximum = Math.max(0, scrollWidth - clientWidth);
  return {left:maximum > 2 && scrollLeft > 2, right:maximum > 2 && scrollLeft < maximum - 2};
}

export function revealNavigationItem({scrollLeft, viewportLeft, viewportWidth, itemLeft, itemRight, maximum, margin=12}) {
  let target = scrollLeft;
  if (itemLeft < viewportLeft + margin) target += itemLeft - viewportLeft - margin;
  else if (itemRight > viewportLeft + viewportWidth - margin) target += itemRight - viewportLeft - viewportWidth + margin;
  return Math.max(0, Math.min(maximum, target));
}
