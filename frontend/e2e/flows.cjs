// Browser E2E: author -> moderation -> publication -> purchase -> session, and hardware checkout -> supplier
// shipment, for each locale. Labels come from the same dictionaries the UI uses (criterion 34).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const BASE = process.env.BASE || "http://localhost:3000";
const out = process.env.SHOTS || "/tmp/e2e-shots";
fs.mkdirSync(out, { recursive: true });

function wav(seconds = 4, fs_ = 48000) {
  const n = seconds * fs_, b = Buffer.alloc(44 + n * 4);
  b.write("RIFF", 0); b.writeUInt32LE(36 + n * 4, 4); b.write("WAVEfmt ", 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20);
  b.writeUInt16LE(2, 22); b.writeUInt32LE(fs_, 24); b.writeUInt32LE(fs_ * 4, 28); b.writeUInt16LE(4, 32); b.writeUInt16LE(16, 34);
  b.write("data", 36); b.writeUInt32LE(n * 4, 40);
  for (let i = 0; i < n; i++) { const v = Math.round(0.2 * Math.sin(2 * Math.PI * 220 * i / fs_) * 32767); b.writeInt16LE(v, 44 + 4 * i); b.writeInt16LE(v, 46 + 4 * i); }
  const p = path.join(out, "e2e.wav"); fs.writeFileSync(p, b); return p;
}

function dict(l) {
  const d = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "messages", `${l}.json`), "utf8"));
  return (k, params = {}) => {
    let s = k.split(".").reduce((o, x) => o[x], d);
    for (const [a, v] of Object.entries(params)) s = s.split(`{${a}}`).join(v);
    return s;
  };
}

let CUR = null;
async function step(name, fn) {
  const t0 = Date.now();
  try { await fn(); console.log(`  ok   ${name} (${Date.now() - t0} ms)`); }
  catch (e) {
    console.log(`  FAIL ${name}: ${e.message.split("\n")[0]}`);
    if (CUR) await CUR.screenshot({ path: `${out}/fail-${name.replace(/\W+/g, "_")}.png`, fullPage: true }).catch(() => {});
    throw e;
  }
}

async function register(browser, L, t, dev) {
  const ctx = await browser.newContext({ locale: L === "ru" ? "ru-RU" : "en-US" });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/${L}/register`);
  const email = `e2e${Date.now()}${Math.random().toString(36).slice(2, 6)}@example.com`;
  await p.getByLabel(t("auth.name")).fill("E2E " + L);
  await p.getByLabel(t("auth.email")).fill(email);
  await p.getByLabel(t("auth.password")).fill("long-password-123");
  if (dev) await p.getByLabel(t("auth.as_developer")).check();
  await p.getByRole("button", { name: t("auth.register") }).click();
  await p.waitForURL(dev ? `**/${L}/developer` : `**/${L}`);
  return { ctx, p, email };
}

async function login(browser, L, t, email) {
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await p.goto(`${BASE}/${L}/login`);
  await p.getByLabel(t("auth.email")).fill(email);
  await p.getByLabel(t("auth.password")).fill("demo-password-1");
  await p.getByRole("button", { name: t("auth.login"), exact: true }).click();
  await p.waitForURL((u) => !u.pathname.endsWith("/login"));
  return { ctx, p };
}

async function run(L) {
  const t = dict(L);
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const slug = `e2e-${L}-${Date.now().toString(36)}`;
  console.log(`[${L}] digital scenario`);
  const dev = await register(browser, L, t, true);
  const d = dev.p;
  CUR = d;
  await step("create app", async () => {
    await d.getByRole("button", { name: t("dev.new_app") }).first().click();
    await d.getByLabel(t("dev.slug")).fill(slug);
    await d.getByLabel(t("dev.author")).fill("E2E Studio");
    await d.getByLabel(t("dev.title_ru")).fill("Тестовая медитация");
    await d.getByLabel(t("dev.title_en")).fill("Test meditation");
    await d.getByRole("button", { name: t("dev.create") }).click();
    await d.waitForURL("**/developer/apps/**");
  });
  await step("upload + real conversion", async () => {
    await d.getByRole("tab", { name: t("dev.tab_audio") }).click();
    await d.getByLabel(t("dev.upload"), { exact: true }).setInputFiles(wav());
    await d.getByText(t("dev.uploaded"), { exact: true }).first().waitFor({ timeout: 30000 });
    await d.getByRole("button", { name: t("dev.start_conversion") }).click();
    await d.locator(".badge", { hasText: t("job_state.completed") }).first().waitFor({ timeout: 90000 });
    await d.getByText(t("qa.carriers")).waitFor({ timeout: 15000 });
    await d.screenshot({ path: `${out}/${L}-converter.png`, fullPage: true });
  });
  await step("details, program, price, submit", async () => {
    await d.getByRole("tab", { name: t("dev.tab_details") }).click();
    await d.getByLabel(t("dev.rights")).check();
    await d.getByRole("button", { name: t("common.save") }).click();
    await d.getByRole("tab", { name: t("dev.tab_program") }).click();
    await d.getByRole("button", { name: t("dev.add_session") }).click();
    await d.getByLabel(t("dev.title_ru")).fill("Сеанс 1");
    await d.getByLabel(t("dev.title_en")).fill("Session 1");
    await d.getByRole("button", { name: t("dev.save_program") }).click();
    await d.getByLabel(t("dev.key")).first().waitFor();
    await d.waitForLoadState("networkidle");
    await d.getByRole("tab", { name: t("dev.tab_price") }).click();
    await d.getByRole("button", { name: t("common.save") }).click();
    await d.getByText(t("dev.current_price")).waitFor();
    await d.getByRole("tab", { name: t("dev.tab_versions") }).click();
    await d.getByRole("button", { name: t("dev.submit") }).click();
    await d.locator(".badge", { hasText: t("version_state.submitted") }).first().waitFor();
  });
  await step("moderator approves", async () => {
    const m = await login(browser, L, t, "moderator@nap.demo");
    await m.p.goto(`${BASE}/${L}/moderation`);
    await m.p.getByText(slug).click();
    await m.p.getByLabel(t("mod.comment")).fill("OK e2e");
    await m.p.getByRole("button", { name: t("mod.approve") }).click();
    await m.p.locator(".badge", { hasText: t("version_state.approved") }).first().waitFor();
    await m.ctx.close();
  });
  await step("author publishes", async () => {
    await d.reload();
    await d.getByRole("button", { name: t("dev.publish") }).click();
    await d.locator(".badge", { hasText: t("version_state.published") }).first().waitFor();
  });
  const buyer = await register(browser, L, t, false);
  const b = buyer.p;
  CUR = b;
  await step("buyer purchases via test checkout", async () => {
    await b.goto(`${BASE}/${L}/apps/${slug}`);
    await b.getByRole("button", { name: t("app.buy") }).click();
    await b.waitForURL("**/mock-checkout/**");
    await b.getByRole("button", { name: t("mock.pay") }).click();
    await b.getByText(t("mock.result_succeeded")).waitFor();
    await b.getByRole("button", { name: t("mock.continue") }).click();
    await b.waitForURL("**/library");
    await b.getByRole("link", { name: t("library.play") }).first().click();
  });
  await step("session: precheck, questionnaire, play, offline stop + sync", async () => {
    await b.getByRole("button", { name: t("player.add_sim") }).click();
    await b.waitForTimeout(500);
    await b.getByRole("button", { name: t("player.prepare") }).click();
    await b.getByRole("button", { name: t("player.continue") }).click();
    await b.getByRole("button", { name: t("player.q_skip") }).click();
    await b.getByRole("button", { name: t("player.play") }).click({ timeout: 30000 });
    await b.waitForTimeout(1500);
    await b.screenshot({ path: `${out}/${L}-player.png`, fullPage: true });
    // connection drops while the session runs: local stop must still work, events sync once afterwards
    await buyer.ctx.setOffline(true);
    await b.waitForTimeout(800);
    await b.getByRole("button", { name: t("player.stop") }).click();
    await b.getByRole("button", { name: t("player.q_skip") }).waitFor();
    await buyer.ctx.setOffline(false);
    await b.evaluate(() => window.dispatchEvent(new Event("online")));
    let st = "";
    for (let i = 0; i < 20 && st !== "cancelled"; i++) {
      await b.waitForTimeout(500);
      st = await b.evaluate(async () => (await (await fetch("/api/v1/me/sessions")).json()).items[0].state);
    }
    if (st !== "cancelled") throw new Error("offline stop not synced: " + st);
    await b.getByRole("button", { name: t("player.q_skip") }).click();
    await b.getByText(t("player.end_cancelled")).waitFor();
  });
  await step("language switch keeps page, session and cart", async () => {
    await b.goto(`${BASE}/${L}/devices/nap-cable-35`);
    await b.getByRole("button", { name: t("cart.add") }).click();
    await b.getByText(t("cart.added")).waitFor();
    const other = L === "ru" ? "en" : "ru";
    const t2 = dict(other);
    await b.getByRole("link", { name: other.toUpperCase(), exact: true }).click();
    await b.waitForURL(`**/${other}/devices/nap-cable-35`);
    await b.getByRole("link", { name: t2("nav.cart", { count: "1" }) }).waitFor();
    const lang = await b.getAttribute("html", "lang");
    if (lang !== other) throw new Error("html lang " + lang);
    await b.getByRole("link", { name: L.toUpperCase(), exact: true }).click();
    await b.waitForURL(`**/${L}/devices/nap-cable-35`);
  });
  console.log(`[${L}] hardware scenario`);
  let orderNumber = "";
  await step("kit builder adds missing parts; checkout", async () => {
    await b.goto(`${BASE}/${L}/kits/nap-starter-kit?app=quiet-harbor`);
    await b.getByRole("button", { name: t("kit.add_missing") }).click();
    await b.getByText(t("kit.added", { count: "4" })).waitFor();
    await b.goto(`${BASE}/${L}/cart`);
    await b.getByRole("button", { name: t("cart.checkout") }).click();
    await b.waitForURL("**/orders?group=**");
    await b.locator("a.card").first().click();
    await b.waitForURL(/\/orders\/[0-9a-f-]{36}$/);
    orderNumber = (await b.locator("h1").innerText()).split(" ").pop();
  });
  await step("international address, rate, payment", async () => {
    await b.getByLabel(t("fields.country") + " *").selectOption("AM");
    await b.getByLabel(t("fields.name") + " *").fill("Anna Petrosyan");
    await b.getByLabel(t("fields.line1") + " *").fill("Abovyan st. 10");
    await b.getByLabel(t("fields.city") + " *").fill("Yerevan");
    await b.getByLabel(t("fields.postal_code")).fill("0001");
    await b.getByLabel(t("fields.phone") + " *").fill("+374 10 000000");
    await b.getByRole("button", { name: t("address.save") }).click();
    await b.getByRole("button", { name: t("orders.calculate") }).click();
    await b.locator('input[name="rate"]').first().click();
    await b.locator(".notice", { hasText: t("orders.selected_rate", { carrier: "", amount: "", min: "", max: "" }).split(":")[0] }).waitFor();
    await b.getByRole("button", { name: new RegExp(t("orders.pay", { amount: "" }).trim()) }).click();
    await b.waitForURL("**/mock-checkout/**");
    await b.getByRole("button", { name: t("mock.pay") }).click();
    await b.getByRole("button", { name: t("mock.continue") }).click();
    await b.locator(".badge", { hasText: t("payment_status.paid") }).first().waitFor();
  });
  await step("supplier: accept, pack, label, hand over", async () => {
    const s = await login(browser, L, t, "seller@nap.demo");
    await s.p.goto(`${BASE}/${L}/seller`);
    await s.p.getByRole("link", { name: orderNumber }).click();
    await s.p.getByRole("button", { name: t("seller.acknowledge") }).click();
    for (const input of await s.p.locator('form:has-text("' + t("seller.new_parcel") + '") input[type=number][max]').all()) {
      await input.fill(await input.getAttribute("max"));
    }
    await s.p.getByRole("button", { name: t("seller.create_parcel") }).click();
    await s.p.getByRole("button", { name: t("seller.buy_label") }).click();
    await s.p.getByRole("button", { name: t("seller.handover") }).click();
    await s.p.locator(".badge", { hasText: t("fulfillment_status.shipped") }).first().waitFor();
    await s.ctx.close();
  });
  await step("buyer sees shipment and tracking", async () => {
    await b.reload();
    await b.locator(".badge", { hasText: t("fulfillment_status.shipped") }).first().waitFor();
    await b.getByText(t("orders.parcels")).waitFor();
    await b.screenshot({ path: `${out}/${L}-order.png`, fullPage: true });
  });
  await browser.close();
}

(async () => {
  const results = {};
  for (const L of (process.env.LOCALES || "ru,en").split(",")) {
    try { await run(L); results[L] = "passed"; } catch { results[L] = "failed"; }
  }
  console.log(JSON.stringify(results));
  process.exit(Object.values(results).every((x) => x === "passed") ? 0 : 1);
})();
