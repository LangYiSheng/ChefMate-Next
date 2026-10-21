// Lucide 1.60.0, vendored locally under vendor/lucide (ISC license).
const names = new Set(`panel-left panel-left-close panel-left-open panel-right plus settings x arrow-up arrow-down arrow-up-right image-plus check chevron-left chevron-right chevron-down chevron-up menu maximize minimize message-circle arrow-left circle-help search play pause square refresh-cw trash-2 chef-hat utensils shopping-bag list-checks timer info circle-alert loader-circle book-open images copy undo-2 corner-down-left camera upload send circle-check circle-x external-link ellipsis check-check circle-stop list-filter cooking-pot layout-grid list sun-moon plug layers sliders-horizontal database code-xml`.split(' '));
const sprite = '/static/vendor/lucide/sprite.svg';
const svgNamespace = 'http://www.w3.org/2000/svg';
const checkedName = name => names.has(name) ? name : 'circle-help';

/** Decorative SVG. Put accessible names on its containing button or link. */
export function icon(name, className = '') {
  const item = document.createElementNS(svgNamespace, 'svg');
  for (const [key, value] of Object.entries({
    class: `icon${className ? ` ${className}` : ''}`, width: '20', height: '20',
    viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false',
  })) item.setAttribute(key, value);
  const use = document.createElementNS(svgNamespace, 'use');
  use.setAttribute('href', `${sprite}#${checkedName(name)}`);
  item.append(use);
  return item;
}

/** Trusted markup from a fixed icon-name allowlist; never includes caller HTML. */
export function iconMarkup(name) {
  return `<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><use href="${sprite}#${checkedName(name)}"></use></svg>`;
}

/** Hydrate declarative placeholders in static HTML using the same library. */
export function hydrateIcons(root = document) {
  for (const target of root.querySelectorAll('[data-icon]')) {
    target.replaceChildren(icon(target.dataset.icon));
  }
}
