// Dock: docked, tabbed, floating, unpinned and popped-out panels, with their theme. Import what you need from here, or
// from the modules directly.

export * from './layout.js';
export { createDock, panelsFrom, escText, TEXT, LAYOUT_KEY, POP_URL, POP_ROOT_ID, THEME_ATTRS } from './dock.js';
export { mountPanelsMenu } from './panels-menu.js';
export { createHelp } from './help.js';
export { createTheme, applyTheme, THEMES, THEME_KEY, THEME_EVENT } from './theme.js';
export { hostDoc, hostDocs, useDoc, byId, onEveryWindow, listenEveryDoc, whenGone, addHostDoc, removeHostDoc } from './host.js';
