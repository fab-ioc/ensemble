// Pop-out windows without the address bar. A page cannot hide the address bar of a window it opens: Chrome and Edge
// always show it in a popup of an ordinary browser window. They leave it out when the page itself runs as an app:
// installed from its web manifest (Install in the address bar, or the button this module drives), or started with
// --app=<url>. Then a window it opens on a page inside the manifest's scope (the dock's popout.html) is an app window
// too: a title bar, no address bar. See README "Pop-out windows without the address bar".
//
//   import { isAppWindow, mountInstallButton } from './dock/src/install.js';
//   mountInstallButton(document.getElementById('install-btn'));   // shown only while the browser offers to install

const MODES = ['standalone', 'window-controls-overlay', 'minimal-ui', 'fullscreen'];

/** Whether this page runs as an app (installed, or --app=): its windows, pop-outs included, have no address bar. */
export function isAppWindow(win = typeof window !== 'undefined' ? window : null) {
  if (!win || typeof win.matchMedia !== 'function') return false;
  return MODES.some((m) => win.matchMedia(`(display-mode: ${m})`).matches);
}

/** Adds `fn` as a 'change' listener on a MediaQueryList (old Safari: addListener) and returns how to remove it. */
function onModeChange(mql, fn) {
  if (!mql) return () => {};
  if (typeof mql.addEventListener === 'function') {
    mql.addEventListener('change', fn);
    return () => mql.removeEventListener('change', fn);
  }
  if (typeof mql.addListener === 'function') {
    mql.addListener(fn);
    return () => mql.removeListener(fn);
  }
  return () => {};
}

/**
 * Drives an "Install app" button: hidden until the browser says the page can be installed (beforeinstallprompt), a
 * click asks the browser to install it, and it hides again once installed or while the page runs as an app — also
 * when the page starts in a browser tab and later turns into one (or back), without a reload: a 'change' listener on
 * each display-mode media query, and 'appinstalled', both re-check and re-show or re-hide the button. Browsers
 * without beforeinstallprompt (Firefox, Safari) never show it. Returns { destroy, canInstall() }.
 */
export function mountInstallButton(button, { win = typeof window !== 'undefined' ? window : null, onInstalled = null } = {}) {
  let offer = null;
  const show = () => { button.hidden = !offer || isAppWindow(win); };
  const onOffer = (e) => { e.preventDefault(); offer = e; show(); }; // keep it for the click; no mini-infobar
  const onInstalledEvent = () => { offer = null; show(); if (typeof onInstalled === 'function') onInstalled(); };
  const onClick = async () => {
    const o = offer;
    if (!o) return;
    offer = null;
    show();
    try {
      await o.prompt();
      const choice = await o.userChoice;
      if (choice && choice.outcome !== 'accepted') { offer = null; show(); }
    } catch { /* the browser declined to prompt */ }
  };
  show();
  if (win && win.addEventListener) {
    win.addEventListener('beforeinstallprompt', onOffer);
    win.addEventListener('appinstalled', onInstalledEvent);
  }
  const mqls = win && typeof win.matchMedia === 'function' ? MODES.map((m) => win.matchMedia(`(display-mode: ${m})`)) : [];
  const stopModeListeners = mqls.map((mql) => onModeChange(mql, show));
  button.addEventListener('click', onClick);
  return {
    canInstall: () => !!offer,
    destroy() {
      if (win && win.removeEventListener) {
        win.removeEventListener('beforeinstallprompt', onOffer);
        win.removeEventListener('appinstalled', onInstalledEvent);
      }
      stopModeListeners.forEach((stop) => stop());
      button.removeEventListener('click', onClick);
    },
  };
}
