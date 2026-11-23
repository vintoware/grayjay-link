"use strict";

const $ = (id) => document.getElementById(id);
const ALL = { origins: ["<all_urls>"] };
const LOOPBACK = { origins: ["http://127.0.0.1/*"] };
const DEFAULT_PORT = 47315;

async function load() {
  const stored = (await browser.storage.local.get("settings")).settings || {};
  const s = { ...GrayjayRules.DEFAULTS, port: DEFAULT_PORT, ...stored,
              services: { ...GrayjayRules.DEFAULTS.services, ...(stored.services || {}) } };
  const permitted = await browser.permissions.contains(ALL);
  $("intercept").checked = s.intercept && permitted;
  $("svc-youtube").checked = s.services.youtube;
  $("svc-twitch").checked = s.services.twitch;
  $("extra").value = s.extraHosts;
  $("port").value = s.port;
}

async function save() {
  const port = parseInt($("port").value, 10);
  const settings = {
    intercept: $("intercept").checked,
    services: { youtube: $("svc-youtube").checked, twitch: $("svc-twitch").checked },
    extraHosts: $("extra").value,
    port: port >= 1024 && port <= 65535 ? port : DEFAULT_PORT,
  };
  await browser.storage.local.set({ settings });
  await browser.runtime.sendMessage({ cmd: "settings-changed" });
}

$("intercept").addEventListener("change", async (e) => {
  // permissions.request must run directly in the click handler.
  if (e.target.checked && !(await browser.permissions.request(ALL))) e.target.checked = false;
  await save();
});
for (const id of ["svc-youtube", "svc-twitch", "extra", "port"]) $(id).addEventListener("change", save);

$("check").addEventListener("click", async () => {
  const el = $("status");
  el.className = "";
  if (!(await browser.permissions.contains(LOOPBACK)) && !(await browser.permissions.request(LOOPBACK))) {
    el.textContent = "The extension needs permission to reach the helper on 127.0.0.1.";
    el.className = "bad";
    return;
  }
  await save();
  el.textContent = "Checking…";
  const r = await browser.runtime.sendMessage({ cmd: "status" });
  if (!r || !r.ok) {
    el.textContent = (r && r.error) || "Helper not reachable.";
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
