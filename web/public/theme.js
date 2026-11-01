// The first frame is the right colour before any stylesheet arrives. This runs from the head,
// synchronously, as its own file: the page's content-security policy allows no inline scripts.
// Always set, even when storage is unavailable, so the stylesheets never see a page without a theme.
var t = 'system';
try {
  t = localStorage.getItem('pilcrow.theme') || 'system';
} catch (e) {}
var dark = t === 'dark' || (t === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
document.documentElement.dataset.theme = dark ? 'dark' : 'light';
// Which engine draws the page: a few layout features are handled per engine in the stylesheets
// (WebKit skips off-screen boxes unreliably inside a scrolling pane, so it gets plain layout there).
try {
  var ua = navigator.userAgent;
  document.documentElement.dataset.engine = /AppleWebKit/.test(ua) && !/Chrome|Chromium|Edg\//.test(ua) ? 'webkit' : 'blink';
} catch (e) {}
