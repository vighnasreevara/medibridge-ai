// Apply the saved theme before first paint (no flash). Falls back to the OS preference.
(function () {
  try {
    var saved = localStorage.getItem('mb-theme');
    var dark = saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  } catch (e) { document.documentElement.dataset.theme = 'light'; }
})();
