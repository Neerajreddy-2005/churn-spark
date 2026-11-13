// Applies the saved theme before first paint (external file so a strict CSP needs no inline script).
try {
  var t = localStorage.getItem('cs-theme')
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t)
} catch (_) { /* storage blocked: follow the system setting */ }
