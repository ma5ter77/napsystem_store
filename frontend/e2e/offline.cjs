// NAP-104: save a program for listening without the internet, play the protected copy with the network cut off,
// see the device in the profile, remove it and check the copy disappears. Chromium, RU.
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://localhost:3000";
const ru = require("../messages/ru.json");
const t = (k) => k.split(".").reduce((o, x) => o[x], ru);

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  const errors = [];
  p.on("pageerror", (e) => errors.push(String(e)));
  await p.goto(`${BASE}/ru`);
  const email = `offline${Date.now()}@example.com`;
  const reg = await p.request.post(`${BASE}/api/v1/auth/register`, { data: { email, password: "long-password-123", display_name: "Offline",
    accept_terms: true, consent_personal_data: true, locale: "ru" } });
  if (!reg.ok()) throw new Error(`register ${reg.status()}`);
  const csrf = (await reg.json()).csrf_token;
  const app = await (await p.request.get(`${BASE}/api/v1/catalog/apps/night-stories`)).json();
  const buy = await p.request.post(`${BASE}/api/v1/checkout/digital`, { data: { app_id: app.id, kind: "free" }, headers: { "x-csrf-token": csrf } });
  if (!buy.ok()) throw new Error(`free ${buy.status()}`);

  await p.goto(`${BASE}/ru/play/night-stories`);
  await p.getByRole("button", { name: t("offline.save") }).click();
  await p.getByText(/Можно слушать без интернета до/).waitFor({ timeout: 60000 });

  await p.goto(`${BASE}/ru/library`);
  const saved = p.getByTestId("saved-programs");
  await saved.getByText(/Harbor at night/).waitFor();
  // nothing decrypted on disk: Cache Storage holds only the NAPX package
  const head = await p.evaluate(async () => {
    const c = await caches.open("nap-napx-v1");
    const [req] = await c.keys();
    const b = new Uint8Array(await (await c.match(req)).arrayBuffer());
    return String.fromCharCode(...b.subarray(0, 4));
  });
  if (head !== "NAPX") throw new Error(`cached copy is not a protected package: ${head}`);

  await ctx.setOffline(true);
  await saved.getByRole("button", { name: t("library.play") }).click();
  const player = p.getByTestId("saved-player");
  await player.getByRole("button", { name: t("player.play") }).click({ timeout: 30000 });
  await p.waitForFunction(() => {
    const el = document.querySelector('[data-testid="saved-player"] .player-time');
    const m = (el?.textContent || "").match(/^(\d+) мин (\d+) с/);
    return m && Number(m[1]) * 60 + Number(m[2]) >= 2;          // the decrypted copy really plays, offline
  }, null, { timeout: 15000 });
  await player.getByRole("button", { name: t("player.pause") }).click();
  await ctx.setOffline(false);

  await p.goto(`${BASE}/ru/account?tab=devices`);
  await p.getByRole("heading", { name: t("offline.devices") }).waitFor();
  await p.getByRole("button", { name: t("offline.revoke"), exact: true }).click();
  await p.getByText(t("offline.revoked"), { exact: true }).waitFor();

  await p.goto(`${BASE}/ru/library`);
  await p.getByText(t("offline.empty")).waitFor({ timeout: 15000 });   // revoked copy removed once online
  if (errors.length) throw new Error(errors.join("\n"));
  console.log(JSON.stringify({ offline: "passed" }));
  await browser.close();
})().catch((e) => { console.error(e); console.log(JSON.stringify({ offline: "failed" })); process.exit(1); });
