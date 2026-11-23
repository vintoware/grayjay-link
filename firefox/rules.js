"use strict";

// Which links get opened in Grayjay when click interception is on.
// Shared by the background script and the content script.
var GrayjayRules = (() => {
  const TWITCH_RESERVED = new Set([
    "directory", "downloads", "drops", "inventory", "jobs", "login", "messages", "p", "payments",
    "prime", "search", "settings", "signup", "store", "subscriptions", "turbo", "videos", "wallet",
  ]);

  const SERVICES = {
    youtube: {
      label: "YouTube",
      hosts: ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"],
      match(u) {
        if (u.hostname === "youtu.be") return u.pathname.length > 1;
        return /^\/(watch|shorts\/|live\/|playlist|@|channel\/|c\/)/.test(u.pathname)
          && !(u.pathname === "/watch" && !u.searchParams.get("v"));
      },
    },
    twitch: {
      label: "Twitch",
      hosts: ["twitch.tv", "www.twitch.tv", "m.twitch.tv"],
      match(u) {
        if (/^\/videos\/\d+/.test(u.pathname)) return true;
        const m = u.pathname.match(/^\/([A-Za-z0-9_]{3,25})\/?$/);
        return !!m && !TWITCH_RESERVED.has(m[1].toLowerCase());
      },
    },
  };

  const DEFAULTS = { intercept: false, services: { youtube: true, twitch: true }, extraHosts: "" };

  function hostIn(hostname, hosts) {
    return hosts.some((h) => hostname === h || hostname.endsWith("." + h));
  }

  function extraHostList(settings) {
    return (settings.extraHosts || "")
      .split(/[\s,]+/)
      .map((h) => h.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, ""))
      .filter(Boolean);
  }

  // Returns the service key ("youtube", "twitch", "extra") a URL belongs to, or null.
  function serviceOf(url, settings) {
    let u;
    try {
      u = url instanceof URL ? url : new URL(url);
    } catch {
      return null;
    }
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    for (const [key, svc] of Object.entries(SERVICES)) {
      if (settings.services[key] && hostIn(u.hostname, svc.hosts)) return svc.match(u) ? key : null;
    }
    return hostIn(u.hostname, extraHostList(settings)) ? "extra" : null;
  }

  function sameSite(a, b) {
    const base = (h) => h.replace(/^(www|m)\./, "");
    return base(a) === base(b) || (/(^|\.)youtube\.com$|^youtu\.be$/.test(a) && /(^|\.)youtube\.com$|^youtu\.be$/.test(b));
  }

  return { SERVICES, DEFAULTS, serviceOf, sameSite };
})();
