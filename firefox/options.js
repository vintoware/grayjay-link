"use strict";

const $ = (id) => document.getElementById(id);
const ALL = { origins: ["<all_urls>"] };

async function load() {
  const stored = (await browser.storage.local.get("settings")).settings || {};
  const s = { ...GrayjayRules.DEFAULTS, ...stored, services: { ...GrayjayRules.DEFAULTS.services, ...(stored.services || {}) } };
  const permitted = await browser.permissions.contains(ALL);
  $("intercept").checked = s.intercept && permitted;
  $("svc-youtube").checked = s.services.youtube;
  $("svc-twitch").checked = s.services.twitch;
  $("extra").value = s.extraHosts;
}

async function save() {
  const settings = {
    intercept: $("intercept").checked,
    services: { youtube: $("svc-youtube").checked, twitch: $("svc-twitch").checked },
    extraHosts: $("extra").value,
  };
  await browser.storage.local.set({ settings });
  await browser.runtime.sendMessage({ cmd: "settings-changed" });
}

$("intercept").addEventListener("change", async (e) => {
  // permissions.request must run directly in the click handler.
  if (e.target.checked && !(await browser.permissions.request(ALL))) e.target.checked = false;
  await save();
});
for (const id of ["svc-youtube", "svc-twitch"]) $(id).addEventListener("change", save);
$("extra").addEventListener("change", save);

$("check").addEventListener("click", async () => {
  const el = $("status");
  el.textContent = "Checking…";
  el.className = "";
  const r = await browser.runtime.sendMessage({ cmd: "status" });
  if (!r || !r.ok) {
    el.textContent = "Helper not reachable: " + ((r && r.error) || "unknown error");
    el.className = "bad";
    return;
  }
  const st = r.status;
  const text = {
    connected: "Connected to " + ((st.device && st.device.name) || "Grayjay") + ".",
    unpaired: "Helper is running but not paired. Pair it from the Grayjay Link widget.",
    "grayjay-not-running": "Paired. Grayjay isn't running; it will be started when you open a link.",
    connecting: "Paired, connecting to Grayjay…",
    rejected: "Grayjay no longer accepts this helper. Pair again from the widget.",
  }[st.state] || st.state;
  el.textContent = text;
  el.className = st.state === "connected" || st.state === "grayjay-not-running" ? "ok" : "bad";
});

load();
