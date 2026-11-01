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

// NAP-025: a small valid PNG for the cover upload (no image libraries needed)
function png(w = 900, h = 675) {
  const zlib = require("zlib");
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(td) >>> 0);
    return Buffer.concat([len, td, crc]);
  };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = 40 + (x % 200); raw[o + 1] = 90; raw[o + 2] = 140 + (y % 100); }
  const p = path.join(out, "cover.png");
  fs.writeFileSync(p, Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]));
  return p;
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
    const slugInput = d.getByLabel(t("dev.slug"));
    await d.getByLabel(t("dev.author")).fill("E2E Studio");
    await d.getByLabel(t("dev.title_ru")).fill("Тестовая медитация");
    await d.getByLabel(t("dev.title_en")).fill("Test meditation");
    // NAP-067: a non-Latin address is stopped in the browser, no request is sent
    await slugInput.fill("медитация");
    let posted = 0;
    const count = (r) => { if (r.method() === "POST" && r.url().endsWith("/api/v1/developer/apps")) posted++; };
    d.on("request", count);
    await d.getByRole("button", { name: t("dev.create") }).click();
    await d.waitForTimeout(300);
    if (await slugInput.evaluate((el) => el.validity.valid)) throw new Error("non-Latin slug passed browser validation");
    if (posted) throw new Error("form was submitted with an invalid slug");
    d.off("request", count);
    // underscores, capitals and spaces are normalised while typing
    await slugInput.fill(slug.toUpperCase().replace(/-/g, "_"));
    if ((await slugInput.inputValue()) !== slug) throw new Error(`slug not normalised: ${await slugInput.inputValue()}`);
    await d.getByRole("button", { name: t("dev.create") }).click();
    await d.waitForURL("**/developer/apps/**");
  });
  await step("upload + real conversion", async () => {
    await d.getByRole("tab", { name: t("dev.tab_audio") }).click();
    // NAP-069: 60 s WAV (~11 MB) goes in two chunks through the Next.js proxy; conversion starts by itself
    await d.getByLabel(t("dev.upload"), { exact: true }).setInputFiles(wav(60));
    await d.getByText(t("dev.uploaded"), { exact: true }).first().waitFor({ timeout: 60000 });
    await d.locator(".badge", { hasText: t("job_state.completed") }).first().waitFor({ timeout: 120000 });
    await d.getByText(t("qa.carriers")).waitFor({ timeout: 15000 });
    await d.screenshot({ path: `${out}/${L}-converter.png`, fullPage: true });
  });
  await step("details, program, price, submit", async () => {
    await d.getByRole("tab", { name: t("dev.tab_details") }).click();
    // NAP-082: the next-step banner lists what is missing; saving is confirmed on screen
    await d.getByText(t("dev.next_fill")).waitFor();
    await d.getByLabel(t("dev.rights")).check();
    await d.getByText(t("dev.unsaved_rights")).waitFor();
    await d.getByRole("button", { name: t("common.save"), exact: true }).click();
    await d.getByText(t("dev.saved_details")).waitFor();
    // NAP-025: own cover, moderated with the version
    await d.locator("input[type=file][accept^='image']").setInputFiles(png());
    await d.getByText(t("dev.cover_saved")).waitFor({ timeout: 30000 });
    await d.getByRole("tab", { name: t("dev.tab_program") }).click();
    // the first finished track is already session 1, titled like the app
    await d.getByLabel(t("dev.track")).waitFor(); // the Program tab is shown (Details also has title fields)
    if ((await d.getByLabel(t("dev.title_ru")).inputValue()) !== "Тестовая медитация") throw new Error("program not auto-filled");
    await d.waitForFunction((lbl) => [...document.querySelectorAll("label")].some((l) => l.textContent.startsWith(lbl) && l.querySelector("select")?.value), t("dev.track"), { timeout: 10000 })
      .catch(() => { throw new Error("auto-filled session has no track"); });
    await d.getByLabel(t("dev.title_ru")).fill("Сеанс 1");
    await d.getByLabel(t("dev.title_en")).fill("Session 1");
    // NAP-068: a session without a track is stopped in the form with a clear message, no 500
    const track = d.getByLabel(t("dev.track"));
    const chosen = await track.inputValue();
    await track.selectOption("");
    await d.getByRole("button", { name: t("dev.save_program") }).click();
    await d.getByText(t("dev.tracks_missing", { list: "1" })).waitFor();
    await track.selectOption(chosen);
    await d.getByRole("button", { name: t("dev.save_program") }).click();
    await d.getByLabel(t("dev.key")).first().waitFor();
    await d.waitForLoadState("networkidle");
    await d.getByRole("tab", { name: t("dev.tab_price") }).click();
    await d.getByRole("button", { name: t("common.save"), exact: true }).click();
    await d.getByText(t("dev.saved_price")).waitFor();
    await d.getByText(t("dev.current_price")).waitFor();
    await d.getByRole("tab", { name: t("dev.tab_versions") }).click();
    await d.getByText(t("dev.check_title")).waitFor();
    await d.getByText(t("dev.next_submit")).waitFor();
    await d.getByRole("button", { name: t("dev.submit"), exact: true }).click();
    await d.getByText(t("dev.done_submitted")).waitFor();
    await d.locator(".badge", { hasText: t("version_state.submitted") }).first().waitFor();
  });
  await step("unknown address shows the localized not-found page (NAP-044)", async () => {
    const r = await d.goto(`${BASE}/${L}/no-such-page-${Date.now()}`);
    if (r.status() !== 404) throw new Error("expected 404, got " + r.status());
    await d.getByRole("heading", { name: t("state.not_found_title") }).waitFor();
    await d.getByRole("link", { name: t("home.cta_apps") }).first().waitFor();
    await d.goBack();
  });
  await step("moderator approves: the app goes live at once (NAP-083)", async () => {
    const m = await login(browser, L, t, "moderator@nap.demo");
    await m.p.goto(`${BASE}/${L}/moderation`);
    await m.p.getByText(t("mod.goes_live_on_approve")).first().waitFor();
    await m.p.getByText(slug).click();
    await m.p.getByLabel(t("mod.comment")).fill("OK e2e");
    await m.p.getByRole("button", { name: t("mod.approve") }).click();
    await m.p.getByText(t("mod.done_published")).waitFor();
    await m.p.getByRole("link", { name: t("mod.open_in_store") }).waitFor();
    await m.p.locator(".badge", { hasText: t("version_state.published") }).first().waitFor();
    await m.ctx.close();
  });
  await step("author sees the app published without another click", async () => {
    await d.reload();
    await d.getByText(t("dev.next_published")).waitFor();
    await d.locator(".badge", { hasText: t("version_state.published") }).first().waitFor();
    // the uploaded cover is now public on the store page
    const r = await d.request.get(`${BASE}/api/v1/catalog/apps/${slug}`);
    const card = await r.json();
    if (!card.cover_url) throw new Error("published app has no cover_url");
    const img = await d.request.get(`${BASE}${card.cover_url}`);
    if (img.status() !== 200 || img.headers()["content-type"] !== "image/webp") throw new Error("cover not served: " + img.status());
  });
  await step("catalog covers: neighbours never share a tone (NAP-040)", async () => {
    await d.goto(`${BASE}/${L}/apps`);
    await d.locator(".grid .card svg[data-tone]").first().waitFor();
    // positions over all cards: an app with an uploaded cover (NAP-025) shows its image and has no tone
    const tones = await d.$$eval(".grid .card", (els) => els.map((e) => e.querySelector("svg[data-tone]")?.getAttribute("data-tone") ?? null));
    tones.forEach((x, i) => { if (x === null) return; for (let k = Math.max(0, i - 4); k < i; k++) if (tones[k] === x) throw new Error(`cards ${k} and ${i} share tone ${x}`); });
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
    // NAP-042: seek bar, remaining time and large back/forward buttons
    await b.getByRole("slider", { name: t("player.seek") }).waitFor();
    await b.getByText(t("player.remaining", { time: "" }).trim()).first().waitFor();
    await b.getByRole("button", { name: t("player.back30") }).click();
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
  await step("rating, review, developer reply (NAP-065)", async () => {
    // paid app without a completed session: the form is not offered, the reason is shown
    await b.goto(`${BASE}/${L}/apps/${slug}`);
    await b.getByText(t("rev.why.no_completed_session")).waitFor();
    // free app: get it, rate it, write a review
    await b.goto(`${BASE}/${L}/apps/night-stories`);
    await b.getByRole("button", { name: t("app.get_free") }).click();
    await b.waitForURL(`**/${L}/library**`);
    await b.goto(`${BASE}/${L}/apps/night-stories`);
    await b.getByText(t("rev.write_title")).waitFor();
    await b.locator(".star-input label").nth(4).click();
    const text = `E2E ${L} ${Date.now().toString(36)}`;
    await b.getByLabel(t("rev.body")).fill(text);
    await b.getByRole("button", { name: t("rev.publish") }).click();
    await b.getByText(t("rev.your_review")).waitFor();
    // the developer answers from the cabinet; the author sees the reply
    const d2 = await login(browser, L, t, "dev2@nap.demo");
    await d2.p.goto(`${BASE}/${L}/developer?tab=reviews`);
    const item = d2.p.locator("article.review", { hasText: text });
    await item.getByLabel(t("rev.reply_label")).fill("Thanks!");
    await item.getByRole("button", { name: t("rev.send_reply") }).click();
    await item.getByText(t("rev.your_reply")).waitFor();
    await d2.ctx.close();
    await b.reload();
    await b.locator("article.review", { hasText: text }).getByText(t("rev.dev_reply")).waitFor();
  });
  await step("refund request from the profile, admin declines with a reason (NAP-024)", async () => {
    await b.goto(`${BASE}/${L}/account?tab=purchases`);
    await b.getByRole("button", { name: t("refund.request") }).first().click();
    await b.getByLabel(t("refund.reason")).fill("E2E: проверка запроса возврата");
    await b.getByRole("button", { name: t("refund.send") }).click();
    await b.getByText(t("refund.sent")).waitFor();
    await b.locator(".badge", { hasText: t("refund_status.requested") }).first().waitFor();
    const a = await login(browser, L, t, "admin@nap.demo");
    await a.p.goto(`${BASE}/${L}/admin?tab=refunds`);
    const card = a.p.getByTestId("refund-request").filter({ hasText: "E2E: проверка запроса возврата" }).first();
    await card.getByLabel(t("admin.refund_note")).fill("E2E decline");
    await card.getByRole("button", { name: t("admin.refund_reject") }).click();
    await a.p.getByTestId("refund-request").filter({ hasText: "E2E: проверка запроса возврата" }).first().waitFor({ state: "detached" });
    await a.ctx.close();
    await b.reload();
    await b.locator(".badge", { hasText: t("refund_status.rejected") }).first().waitFor();
    await b.getByText("E2E decline").waitFor();
  });
  await step("developer analytics: funnel with page views, small groups hidden (NAP-027)", async () => {
    await d.goto(`${BASE}/${L}/developer?tab=analytics`);
    const sel = d.getByLabel(t("an.choose_app"));
    await sel.selectOption(await sel.locator("option", { hasText: slug }).getAttribute("value"));
    const funnel = d.getByRole("list", { name: t("an.funnel") });
    await funnel.getByText(t("an.step_views")).waitFor();
    // the buyer opened the app page once: one unique view; one listener is below the group threshold
    await funnel.locator("li").first().locator(".funnel-value", { hasText: /^1/ }).waitFor();
    await funnel.locator("li").nth(1).getByText(t("an.hidden", { n: "5" })).waitFor();
    await d.getByRole("img", { name: t("an.weekly") }).waitFor();
    await d.screenshot({ path: `${out}/${L}-analytics.png`, fullPage: true });
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
  await step("saved address: one click on the next order, listed in the account (NAP-023)", async () => {
    await b.goto(`${BASE}/${L}/devices/nap-cable-35`);
    await b.getByRole("button", { name: t("cart.add") }).first().click();
    await b.getByRole("link", { name: t("cart.added") }).waitFor();
    await b.goto(`${BASE}/${L}/cart`);
    await b.getByRole("button", { name: t("cart.checkout") }).click();
    await b.waitForURL("**/orders?group=**");
    await b.locator("a.card").first().click();
    await b.waitForURL(/\/orders\/[0-9a-f-]{36}$/);
    const row = b.getByTestId("saved-addresses").locator("li", { hasText: "Abovyan st. 10" }).first();
    await row.getByRole("button", { name: t("address.use") }).click();
    await b.getByRole("button", { name: t("orders.calculate") }).waitFor();
    await b.goto(`${BASE}/${L}/account?tab=addresses`);
    await b.getByTestId("saved-addresses").getByText("Abovyan st. 10").first().waitFor();
  });
  await step("product ratings: verified purchases only (NAP-079)", async () => {
    await b.goto(`${BASE}/${L}/devices/nap-studio-headphones`);
    await b.getByRole("heading", { name: t("rev.title_section") }).waitFor();
    await b.getByText(t("rev.verified")).first().waitFor();
    // the order is shipped but not delivered yet: rating is not offered, the reason is shown
    await b.goto(`${BASE}/${L}/devices/nap-cable-35`);
    await b.getByText(t("rev.why.no_delivered_purchase")).waitFor();
  });
  await step("supplier lists a new product, moderator approves, it is in the catalog (NAP-022)", async () => {
    const stamp = Date.now().toString(36);
    const title = `E2E ${L} ${stamp}`;
    const s = await login(browser, L, t, "seller2@nap.demo");
    CUR = s.p;
    await s.p.goto(`${BASE}/${L}/seller?tab=products`);
    await s.p.getByRole("button", { name: t("sp.new") }).click();
    await s.p.getByLabel(t("fields.brand")).fill("E2E");
    await s.p.getByLabel(t("fields.model")).fill(`${L}-${stamp}`);
    await s.p.getByRole("button", { name: t("sp.create") }).click();
    await s.p.waitForURL(/\/seller\/products\/[0-9a-f-]+/);
    await s.p.getByLabel(t("sp.price")).fill("49.90");
    await s.p.getByLabel(t("sp.weight")).fill("300");
    for (const k of ["length_mm", "width_mm", "height_mm"]) await s.p.getByLabel(t(`fields.${k}`), { exact: true }).fill("150");
    await s.p.getByLabel(t("sp.origin")).fill("DE");
    await s.p.getByRole("button", { name: t("sp.add_sku") }).click();
    await s.p.getByText(t("sp.sku_added")).waitFor();
    await s.p.getByRole("tab", { name: t("sp.tab_basics") }).click();
    await s.p.getByLabel(t("sp.destinations")).fill("DE, RS, AM");
    await s.p.getByRole("button", { name: t("common.save") }).click();
    await s.p.getByText(t("sp.saved")).waitFor();
    await s.p.getByRole("tab", { name: t("sp.tab_texts") }).click();
    const f = s.p.locator(`fieldset[lang=${L}]`);
    await f.getByLabel(t("sp.title")).fill(title);
    await f.getByLabel(t("sp.description")).fill("E2E description");
    await f.getByLabel(t("sp.in_box")).fill("Item 1\nItem 2");
    await f.getByRole("button", { name: t("common.save") }).click();
    await s.p.getByText(t("sp.saved")).waitFor();
    // NAP-047: two photos, the second one becomes the main photo
    await s.p.getByRole("tab", { name: t("sp.tab_photos") }).click();
    for (const [w, h] of [[900, 700], [1000, 800]]) {
      await s.p.getByLabel(t("ph.add")).setInputFiles(png(w, h));
      await s.p.getByText(t("ph.added")).waitFor();
    }
    await s.p.getByTestId("photo-list").locator("li").nth(1).locator("img").waitFor();
    await s.p.getByRole("button", { name: t("ph.left", { n: "2" }) }).click();
    await s.p.getByText(t("ph.saved_order")).waitFor();
    await s.p.getByRole("tab", { name: t("sp.tab_submit") }).click();
    await s.p.getByRole("button", { name: t("sp.submit") }).click();
    await s.p.getByText(t("sp.done_submitted")).waitFor();
    await s.p.locator(".badge", { hasText: t("product_status.submitted") }).first().waitFor();
    const m = await login(browser, L, t, "moderator@nap.demo");
    CUR = m.p;
    await m.p.goto(`${BASE}/${L}/moderation`);
    await m.p.getByText(title).click();
    await m.p.getByRole("button", { name: t("mod.approve") }).click();
    await m.p.getByText(t("mod.product_done_approve")).waitFor();
    await m.ctx.close();
    await b.goto(`${BASE}/${L}/devices/e2e-${L}-${stamp}`);
    await b.getByRole("heading", { name: title }).waitFor();
    await b.getByText("Item 2").waitFor();
    const gallery = b.getByRole("region", { name: t("ph.gallery") });
    await gallery.locator(".gallery-main img").waitFor();
    if (!(await gallery.locator(".gallery-main img").evaluate((im) => im.complete && im.naturalWidth === 1000))) throw new Error("main photo is not the reordered one");
    await gallery.getByRole("button", { name: t("ph.next") }).click();
    await gallery.getByText("2 / 2").waitFor();
    await s.p.reload();
    await s.p.getByText(t("sp.next_published")).waitFor();
    await s.ctx.close();
  });
  await step("moderator manages the catalog: stop and resume sales with a reason (NAP-084)", async () => {
    const m = await login(browser, L, t, "moderator@nap.demo");
    CUR = m.p;
    await m.p.goto(`${BASE}/${L}/moderation?tab=apps`);
    await m.p.getByPlaceholder(t("mm.search")).fill(slug);
    await m.p.locator("table a").first().click();
    await m.p.waitForURL(/moderation\/catalog\/apps\//);
    await m.p.locator(".panel label.field", { hasText: t("mm.reason_hint") }).locator("textarea").fill("E2E check");
    await m.p.getByRole("button", { name: t("dev.stop_sales") }).click();
    await m.p.getByText(t("mm.done_sales_stopped")).waitFor();
    await m.p.locator(".badge", { hasText: t("mm.sales_stopped") }).first().waitFor();
    await m.p.getByRole("button", { name: t("dev.resume_sales") }).click();
    await m.p.getByText(t("mm.done_sales_resumed")).waitFor();
    await m.p.getByText("app.mod_sales_withdrawn").waitFor();  // change history
    await m.ctx.close();
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
