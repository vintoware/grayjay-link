"use strict";

// Talks to the grayjay-link daemon's loopback endpoint (http://127.0.0.1:<port>/v1/...).
// The daemon runs while the Grayjay Link Plasma widget exists.
const DEFAULT_PORT = 47315;
const SCRIPT_ID = "grayjay-link-intercept";
const NOT_RUNNING = "Grayjay Link isn't running. It runs while the Grayjay Link widget is on your panel or desktop.";

async function getSettings() {
  const stored = (await browser.storage.local.get("settings")).settings || {};
  return { ...GrayjayRules.DEFAULTS, port: DEFAULT_PORT, ...stored,
           services: { ...GrayjayRules.DEFAULTS.services, ...(stored.services || {}) } };
}

async function callDaemon(cmd, body = {}) {
  const { port } = await getSettings();
  let resp;
  try {
    resp = await fetch(`http://127.0.0.1:${port}/v1/${cmd}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Grayjay-Link": "1" },
      body: JSON.stringify(body),
      cache: "no-store",
      credentials: "omit",
    });
  } catch (e) {
    return { ok: false, error: NOT_RUNNING };
  }
  try {
    return await resp.json();
  } catch (e) {
    return { ok: false, error: `Unexpected response from port ${port} (HTTP ${resp.status}).` };
  }
}

async function notify(title, message) {
  await browser.notifications.create({ type: "basic", iconUrl: "icons/grayjay-link.svg", title, message });
}

async function openInGrayjay(url) {
  const resp = await callDaemon("open", { url });
  if (!resp.ok) await notify("Couldn't open in Grayjay", resp.error || "Unknown error");
  return resp;
}

// Keep the click-interception content script registered iff it's enabled and permitted.
async function syncContentScript() {
  const settings = await getSettings();
  const permitted = await browser.permissions.contains({ origins: ["<all_urls>"] });
  const registered = (await browser.scripting.getRegisteredContentScripts({ ids: [SCRIPT_ID] })).length > 0;
  const want = settings.intercept && permitted;
  if (want && !registered) {
    await browser.scripting.registerContentScripts([{
      id: SCRIPT_ID,
      matches: ["<all_urls>"],
      js: ["rules.js", "content.js"],
      runAt: "document_start",
      allFrames: true,
    }]);
  } else if (!want && registered) {
    await browser.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] });
  }
}

browser.runtime.onInstalled.addListener(() => {
  browser.menus.create({ id: "open-link", title: "Open link in Grayjay", contexts: ["link"] });
  browser.menus.create({ id: "open-page", title: "Open page in Grayjay", contexts: ["page", "video"] });
});

browser.menus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "open-link") openInGrayjay(info.linkUrl);
  else if (info.menuItemId === "open-page") openInGrayjay(info.pageUrl || tab.url);
});

browser.action.onClicked.addListener((tab) => {
  if (tab && tab.url) openInGrayjay(tab.url);
});

browser.runtime.onMessage.addListener((msg) => {
  if (msg && msg.cmd === "open" && typeof msg.url === "string") return openInGrayjay(msg.url);
  if (msg && msg.cmd === "status") return callDaemon("status");
  if (msg && msg.cmd === "settings-changed") return syncContentScript();
  return undefined;
});

browser.permissions.onRemoved.addListener(syncContentScript);
browser.permissions.onAdded.addListener(syncContentScript);
browser.runtime.onStartup.addListener(syncContentScript);
syncContentScript();
