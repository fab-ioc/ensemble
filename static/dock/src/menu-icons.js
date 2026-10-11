// The panel menu's built-in icons and the app override contract live here. SVG markup is copied into each title bar;
// an Element supplied by an app remains in its original place.
const svg = (body) => `<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true" focusable="false">${body}</svg>`;

export const MENU_BUTTON_SVGS = Object.freeze({
  dots: svg('<circle cx="3" cy="6" r="1"/><circle cx="6" cy="6" r="1"/><circle cx="9" cy="6" r="1"/>'),
  vdots: svg('<circle cx="6" cy="3" r="1"/><circle cx="6" cy="6" r="1"/><circle cx="6" cy="9" r="1"/>'),
  lines: svg('<path d="M2 3h8M2 6h8M2 9h8"/>'),
  vlines: svg('<path d="M3 2v8M6 2v8M9 2v8"/>'),
  gear: svg('<circle cx="6" cy="6" r="2"/><path d="M6 1v1M6 10v1M1 6h1M10 6h1M2.5 2.5l.7.7m5.6 5.6.7.7m0-7-.7.7m-5.6 5.6-.7.7"/>'),
});

export const MENU_CARET_SVG = '<svg class="dk-panel-menu-arrow" viewBox="0 0 10 10" aria-hidden="true"><path d="m2 3.5 3 3 3-3"/></svg>';

export function iconMarkup(value) {
  if (typeof value === 'string') return value;
  return value?.nodeType === 1 ? value.outerHTML : '';
}

export function menuIconVisual(panel, config) {
  const place = config?.place === 'start' ? 'start' : 'end';
  const fallback = place === 'start' ? 'panel' : 'dots';
  let source = panel?.menuIcon !== undefined ? panel.menuIcon : config?.icon;
  if (typeof source === 'function') source = source(panel);
  if (source == null) source = fallback;
  let kind = source === 'panel' ? 'panel' : Object.hasOwn(MENU_BUTTON_SVGS, source) ? source : 'custom';
  let markup = kind === 'panel' ? iconMarkup(panel?.icon)
    : kind === 'custom' ? (typeof source === 'string' && /^\s*<svg\b/i.test(source) || source?.nodeType === 1 ? iconMarkup(source) : '')
      : MENU_BUTTON_SVGS[kind];
  if (kind === 'custom' && !markup) { kind = 'gear'; markup = MENU_BUTTON_SVGS.gear; }
  return { kind, markup, caret: typeof config?.caret === 'boolean' ? config.caret : kind === 'panel' };
}
