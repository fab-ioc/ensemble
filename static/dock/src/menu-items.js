// The app's own items in a panel's ⋯ menu (v0.9.0): what a menuItems hook returns, made safe to draw. dock.js asks the
// hook each time the menu opens, draws the items above its own with menuItemsHtml, and finds a pick with itemAt.

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SEP = '<div class="dk-menu-sep" role="separator"></div>';
const ARROW = '<span class="dk-menu-arrow" aria-hidden="true">▸</span>';

/**
 * The items as Dock draws them: [{ id, label, title, disabled, checked, sub, run }] and { sep: true }.
 * Left out: anything that is not an object, an item without a label, a separator that would be first, last or next to
 * another. `id` is a string (the label when it has none); `checked` is kept only as a boolean (null: not a checkbox);
 * `run` only as a function. A submenu is one level deep, as Dock's own are: an item inside one keeps no `sub`. An item
 * whose `sub` has no items is shown disabled, as a plain item (no ▸, no submenu to announce).
 */
export function normalizeMenuItems(list, depth = 0) {
  if (!Array.isArray(list)) return [];
  const out = [];
  for (const it of list) {
    if (!it || typeof it !== 'object') continue;
    if (it.sep) {
      if (out.length && !out[out.length - 1].sep) out.push({ sep: true });
      continue;
    }
    if (it.label === undefined || it.label === null || String(it.label) === '') continue;
    const item = {
      id: String(it.id ?? it.label), label: String(it.label), title: it.title == null ? '' : String(it.title),
      disabled: !!it.disabled, checked: typeof it.checked === 'boolean' ? it.checked : null, sub: null,
      run: typeof it.run === 'function' ? it.run : null,
    };
    if (depth === 0 && it.sub != null) {
      const items = normalizeMenuItems(it.sub, 1);
      item.checked = null;
      if (items.length) item.sub = items;
      else item.disabled = true;
    }
    out.push(item);
  }
  while (out.length && out[out.length - 1].sep) out.pop();
  return out;
}

/**
 * The menu buttons of normalised `items`. Each carries data-dk-app, its place ("2", or "2.0" in 2's submenu); one with
 * a submenu also data-dk-sub="app:2". Labels and titles are text.
 */
export function menuItemsHtml(items, path = '') {
  return items.map((it, i) => {
    if (it.sep) return SEP;
    const key = path + i;
    const box = it.checked !== null && !it.sub;
    let a = `type="button" role="${box ? 'menuitemcheckbox' : 'menuitem'}" data-dk-app="${key}"`;
    if (box) a += ` aria-checked="${it.checked}"`;
    if (it.disabled) a += ' aria-disabled="true"';
    if (it.title) a += ` title="${esc(it.title)}"`;
    if (it.sub) a += ` aria-haspopup="menu" aria-expanded="false" data-dk-sub="app:${key}"`;
    return `<button ${a}>${esc(it.label)}${it.sub ? ARROW : ''}</button>`;
  }).join('');
}

/** The item at `key` ("2" or "2.0") in normalised `items`; null if there is none. */
export function itemAt(items, key) {
  let list = items;
  let it = null;
  for (const part of String(key).split('.')) {
    it = list && list[Number(part)];
    if (!it || it.sep) return null;
    list = it.sub;
  }
  return it;
}
