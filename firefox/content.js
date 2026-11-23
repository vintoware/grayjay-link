"use strict";

// Opens supported links in Grayjay on a plain left click. Links within the same
// site (e.g. moving between videos on youtube.com) are left alone, as are
// modified clicks (Ctrl/Shift/middle click), so "open in new tab" still works.
(() => {
  let settings = GrayjayRules.DEFAULTS;
  browser.storage.local.get("settings").then((s) => {
    if (s.settings) settings = { ...settings, ...s.settings, services: { ...settings.services, ...(s.settings.services || {}) } };
  });
  browser.storage.onChanged.addListener((changes) => {
    if (changes.settings && changes.settings.newValue) {
      settings = { ...GrayjayRules.DEFAULTS, ...changes.settings.newValue };
    }
  });

  document.addEventListener("click", (e) => {
    if (!settings.intercept || e.defaultPrevented || e.button !== 0) return;
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    const a = e.target instanceof Element ? e.target.closest("a[href]") : null;
    if (!a) return;
    let url;
    try {
      url = new URL(a.href, location.href);
    } catch {
      return;
    }
    if (!GrayjayRules.serviceOf(url, settings)) return;
    if (GrayjayRules.sameSite(url.hostname, location.hostname)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    browser.runtime.sendMessage({ cmd: "open", url: url.href });
  }, true);
})();
