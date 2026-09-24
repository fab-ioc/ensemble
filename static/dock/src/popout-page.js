// The pop-out page (popout.html) as text, for createDock's popHtml option: the dock opens it from a blob: URL, so
// the app need not serve popout.html. test/needs.test.js checks that the two are the same page.

export const POP_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>Panel</title>
<script>
// Dock's pop-out page: one panel of the app, popped out of it. Serve this file from the same origin as the app's page
// (createDock's popUrl, by default "popout.html" beside the page). It has no modules of its own: the main window moves
// the panel into #dk-pop-root, keeps drawing it, and copies its stylesheets and theme in. If the main window closes or
// reloads, this one closes too; if the browser will not close it, it empties itself, so it never holds buttons over
// stale state.
(function () {
  try {
    var from = window.opener && window.opener.document.documentElement;
    if (from) ['data-theme', 'data-scheme', 'class', 'style'].forEach(function (a) {
      var v = from.getAttribute(a);
      if (v !== null) document.documentElement.setAttribute(a, v);
    });
  } catch (e) { /* not opened by the app */ }
  function orphaned() {
    try {
      var o = window.opener;
      if (!o || o.closed) return true;
      return !!window.__dockPopKey && o.__dockPopKey !== window.__dockPopKey;
    } catch (e) { return true; }
  }
  setInterval(function () {
    if (!orphaned()) return;
    var root = document.getElementById('dk-pop-root');
    if (root && !root.querySelector('.dk-pop-orphan')) {
      root.innerHTML = '<p class="dk-pop-orphan">The main window closed or reloaded, so this window shows nothing live. Close it.</p>';
    }
    window.close();
  }, 500);
})();
</script>
<style>html, body { margin: 0; height: 100%; }</style>
</head>
<body class="dk-popwin">
<div id="dk-pop-root" class="dk-pop-root"><p class="dk-pop-wait">Opening…</p></div>
</body>
</html>
`;
