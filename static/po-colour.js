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
      const token = pick(projects, pid);
      const kind = token.match(TOKEN)[1];
      let fill = token, hex = vars.getPropertyValue(`--c-${kind}-bold`).trim();
      let ink = contrast(hex, '#fff') >= 4.5 ? '#fff' : DARK;
      if (contrast(hex, ink) < 4.5) {
        fill = `var(--c-${kind}-fg)`;
        hex = vars.getPropertyValue(`--c-${kind}-fg`).trim();
        ink = contrast(hex, '#fff') >= 4.5 ? '#fff' : DARK;
      }
      el.style.backgroundColor = fill;
      el.style.color = ink;
      el.dataset.poFill = fill;
    }
  }
  return { COLOURS, pick, paint, contrast };
})();
