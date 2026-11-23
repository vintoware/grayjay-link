"use strict";

const HOST = "grayjay_link";
const SCRIPT_ID = "grayjay-link-intercept";

async function getSettings() {
  const stored = await browser.storage.local.get("settings");
  return { ...GrayjayRules.DEFAULTS, ...(stored.settings || {}),
           services: { ...GrayjayRules.DEFAULTS.services, ...((stored.settings || {}).services || {}) } };
}

async function notify(title, message) {
  await browser.notifications.create({ type: "basic", iconUrl: "icons/grayjay-link.svg", title, message });
}

async function openInGrayjay(url) {
  let resp;
  try {
    resp = await browser.runtime.sendNativeMessage(HOST, { cmd: "open", url });
  } catch (e) {
    await notify("Grayjay Link isn't installed", "The grayjay-link helper could not be started. Run install.sh from the grayjay-link repository.");
    return { ok: false, error: String(e) };
  }
  if (!resp || !resp.ok) {
    await notify("Couldn't open in Grayjay", (resp && resp.error) || "Unknown error");
  }
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
  if (msg && msg.cmd === "status") {
    return browser.runtime.sendNativeMessage(HOST, { cmd: "status" }).catch((e) => ({ ok: false, error: String(e) }));
  }
  if (msg && msg.cmd === "settings-changed") return syncContentScript();
  return undefined;
});

browser.permissions.onRemoved.addListener(syncContentScript);
browser.permissions.onAdded.addListener(syncContentScript);
browser.runtime.onStartup.addListener(syncContentScript);
syncContentScript();
