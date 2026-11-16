// The project-list square and every PO label use this one colour pick.
// `projects` is the /api/projects order, before the person's pin/order display choices.
const ProjectColour = (() => {
  const COLOURS = ['var(--c-progress-bold)', 'var(--c-success-bold)',
    'var(--c-warning-bold)', 'var(--c-discovery-bold)'];
  const TOKEN = /^var\(--c-(progress|success|warning|discovery)-bold\)$/;
  const DARK = '#1D2125';

  function pick(projects, pid) {
    const registered = (projects || []).filter(p => p.registered);
    const i = registered.findIndex(p => p.id === pid);
    if (i >= 0) return COLOURS[i % COLOURS.length];
    let h = 0;
    for (const ch of String(pid || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return COLOURS[h % COLOURS.length];
  }
  function rgb(s) {
    s = String(s || '').trim();
    if (/^#[0-9a-f]{6}$/i.test(s)) return [1, 3, 5].map(i => parseInt(s.slice(i, i + 2), 16));
    if (/^#[0-9a-f]{3}$/i.test(s)) return [1, 2, 3].map(i => parseInt(s[i] + s[i], 16));
    const m = s.match(/^rgba?\(\s*(\d+(?:\.\d+)?)\s*,?\s+(\d+(?:\.\d+)?)\s*,?\s+(\d+(?:\.\d+)?)/i);
    return m ? m.slice(1, 4).map(Number) : null;
  }
  function luminance(s) {
    const c = rgb(s);
    if (!c) return null;
    const [r, g, b] = c.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
    return .2126 * r + .7152 * g + .0722 * b;
  }
  function contrast(a, b) {
    const x = luminance(a), y = luminance(b);
    return x == null || y == null ? 0 : (Math.max(x, y) + .05) / (Math.min(x, y) + .05);
  }
  // A colour's fill and ink in the theme now painted: the bold shade with white or
  // dark ink at 4.5:1, else its -fg shade. The PO label and the project tile both use it.
  function shade(kind, vars) {
    let fill = `var(--c-${kind}-bold)`, hex = vars.getPropertyValue(`--c-${kind}-bold`).trim();
    let ink = contrast(hex, '#fff') >= 4.5 ? '#fff' : DARK;
    if (contrast(hex, ink) < 4.5) {
      fill = `var(--c-${kind}-fg)`;
      hex = vars.getPropertyValue(`--c-${kind}-fg`).trim();
      ink = contrast(hex, '#fff') >= 4.5 ? '#fff' : DARK;
    }
    return { fill, ink };
  }
  // #370 (P356): the project tile, drawn by CSS from root variables per colour:
  // --pt-<kind>-f fill, -i ink, -g the glossy gradient, -s the glossy letter's shadow.
  // The gradient runs away from the ink (white ink: deeper at the foot; dark ink:
  // lighter at the top), so the letter keeps the flat tile's contrast.
  const KINDS = ['progress', 'success', 'warning', 'discovery'];
  function tileVars(root) {
    root = root || document.documentElement;
    const vars = getComputedStyle(root);
    for (const kind of KINDS) {
      const { fill, ink } = shade(kind, vars), white = ink === '#fff';
      root.style.setProperty(`--pt-${kind}-f`, fill);
      root.style.setProperty(`--pt-${kind}-i`, ink);
      root.style.setProperty(`--pt-${kind}-g`, white
        ? `linear-gradient(170deg, ${fill} 0%, ${fill} 35%, color-mix(in srgb, ${fill} 62%, black) 100%)`
        : `linear-gradient(170deg, color-mix(in srgb, ${fill} 50%, white) 0%, ${fill} 70%)`);
      root.style.setProperty(`--pt-${kind}-s`, white ? '0 1px 1px rgba(0,0,0,.35)' : 'none');
    }
  }
  // A tile's letters: the first letter of the first two words (opTen QA: OQ), or of
  // the one word (opTen: O).
  function initials(name) {
    const w = String(name || '').split(/[\s_\-]+/).map(x => (x.match(/[\p{L}\p{N}]/u) || [''])[0]).filter(Boolean);
    return w.slice(0, 2).join('').toUpperCase();
  }
  function paint(root, projects, ownId) {
    if (!root || !projects) return;
    const vars = getComputedStyle(document.documentElement);
    for (const el of root.querySelectorAll('.rl.po')) {
      let pid = el.dataset.poProject || '';
      if (!pid && el.dataset.poName) {
        const matches = projects.filter(p => p.name === el.dataset.poName);
        if (matches.length === 1) pid = matches[0].id;
      }
      if (!pid && !el.hasAttribute('data-po-name')) pid = ownId || '';
      const pj = projects.find(p => p.id === pid);
      if (!pj || (pj.icon && pj.icon.v)) {
        el.style.removeProperty('background-color');
        el.style.removeProperty('color');
        el.removeAttribute('data-po-fill');
        continue;
      }
      const { fill, ink } = shade(pick(projects, pid).match(TOKEN)[1], vars);
      el.style.backgroundColor = fill;
      el.style.color = ink;
      el.dataset.poFill = fill;
    }
  }
  return { COLOURS, KINDS, pick, paint, contrast, shade, tileVars, initials };
})();
