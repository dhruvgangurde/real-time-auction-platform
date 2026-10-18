/* global localStorage, document */
// The colour theme the viewer chose (src/app/theme.ts), applied before first paint: a classic
// script in <head>, blocking but tiny, because the CSP allows no inline script. System (no
// stored choice) leaves data-theme unset, and the tokens follow the device.
try {
  const theme = localStorage.getItem('theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.setAttribute('data-theme', theme);
} catch {
  // storage blocked: System
}
