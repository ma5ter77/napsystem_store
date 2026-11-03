// Browser E2E for the Audio Studio (spec v2.1 M1): upload an immutable 24-bit source with provenance, create a
// 6-bus project, place the source and a binaural pair, autosave, edit in the inspector, preview through the
// server render and export a verified 5.1 WAV whose per-channel report matches the routing.
const { chromium } = require("playwright");
const fs = require("fs");
const BASE = process.env.BASE || "http://localhost:3000", OUT = process.env.SHOTS || "/tmp/e2e-shots";
fs.mkdirSync(OUT, { recursive: true });
function wav24(p, secs = 12, fs_ = 48000) {
  const n = secs * fs_, b = Buffer.alloc(44 + n * 3);
  b.write("RIFF", 0); b.writeUInt32LE(36 + n * 3, 4); b.write("WAVEfmt ", 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20);
  b.writeUInt16LE(1, 22); b.writeUInt32LE(fs_, 24); b.writeUInt32LE(fs_ * 3, 28); b.writeUInt16LE(3, 32); b.writeUInt16LE(24, 34);
  b.write("data", 36); b.writeUInt32LE(n * 3, 40);
  for (let i = 0; i < n; i++) { const env = 0.5 + 0.5 * Math.sin(2 * Math.PI * 0.4 * i / fs_); const v = Math.round(0.3 * env * Math.sin(2 * Math.PI * 180 * i / fs_) * 8388607); b.writeIntLE(v, 44 + 3 * i, 3); }
  fs.writeFileSync(p, b); return p;
}
(async () => {
  const src = wav24(`${OUT}/voice-24bit.wav`);
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const ctx = await browser.newContext({ locale: "ru-RU", viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errs = []; page.on("console", (m) => m.type() === "error" && errs.push(m.text())); page.on("pageerror", (e) => errs.push(String(e)));
  const email = `studio${Date.now()}@example.com`;
  const r = await ctx.request.post(`${BASE}/api/v1/auth/register`, { headers: { "x-locale": "ru" }, data: { email, password: "long-password-123", display_name: "Автор", as_developer: true, accept_terms: true, consent_personal_data: true, locale: "ru" } });
  if (r.status() !== 201) throw new Error("register " + r.status() + await r.text());
  await page.goto(`${BASE}/ru/developer/studio`);
  await page.getByRole("heading", { name: "Аудиостудия" }).waitFor();
  await page.screenshot({ path: `${OUT}/studio-1-empty.png`, fullPage: true });
  // upload a source with provenance
  await page.getByRole("button", { name: "Загрузить исходник" }).first().click();
  await page.locator('input[type=file]').setInputFiles(src);
  await page.getByLabel("Происхождение").fill("собственная запись голоса");
  await page.getByLabel("Права").fill("все права мои");
  await page.locator("form").getByRole("button", { name: "Загрузить исходник" }).click();
  await page.getByText("voice-24bit.wav").waitFor({ timeout: 30000 });
  // project
  await page.getByRole("button", { name: "Новый проект" }).click();
  await page.getByLabel("Название").fill("Фокус · 6 шин");
  await page.getByLabel("Шины").selectOption("6");
  await page.screenshot({ path: `${OUT}/studio-2-list.png`, fullPage: true });
  await page.getByRole("button", { name: "Создать проект" }).click();
  await page.waitForURL(/studio\/[0-9a-f-]{36}/);
  await page.getByText("Сохранено · ревизия 1").waitFor();
  // place voice on VOICE_CENTER, add binaural pair
  await page.locator(".studio-head", { hasText: "Голос" }).click();
  await page.getByRole("button", { name: "Добавить: voice-24bit.wav" }).click();
  await page.getByRole("button", { name: "Бинауральный" }).click();
  await page.getByText(/Сохранено · ревизия 3|Сохранено · ревизия 2/).waitFor({ timeout: 10000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/studio-3-editor.png`, fullPage: false });
  // select the binaural left clip and lower the beat, check autosave + undo
  await page.locator(".studio-clip.is-gen").first().click();
  const delta = page.locator(".studio-inspector label", { hasText: "Ритм (разница Л/П)" }).locator("input");
  await delta.fill("6"); await delta.press("Enter");
  await page.getByText(/Сохранено · ревизия [3-9]/).waitFor();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/studio-4-inspector.png` });
  // preview
  await page.getByRole("button", { name: "Слушать" }).click();
  await page.getByRole("button", { name: "Стоп" }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(800);
  const tc = await page.locator(".studio-tc").textContent();
  await page.getByRole("button", { name: "Стоп" }).click();
  // export through the 5.1 chair profile
  await page.waitForTimeout(1000);
  await page.getByRole("button", { name: "Экспортировать WAV" }).click();
  await page.locator(".studio-job .badge", { hasText: "готово" }).waitFor({ timeout: 60000 });
  await page.locator(".studio-render").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/studio-5-export.png`, fullPage: true });
  const report = await page.locator(".studio-job").first().innerText();
  // NAP-110: the verified export goes to an app as its chair 5.1 version
  await page.getByTestId("to-app").getByText("сначала создайте приложение").waitFor();
  const made = await page.evaluate(async () => {
    const tok = (document.cookie.match(/(?:^|; )nap_csrf=([^;]+)/) || [])[1] || "";
    const r = await fetch("/api/v1/developer/apps", { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": tok },
      body: JSON.stringify({ slug: "studio-" + Date.now().toString(36), author_name: "Автор", metadata: { ru: { title: "Из студии" } } }) });
    return { status: r.status, body: await r.json() };
  });
  if (made.status !== 201) throw new Error("create app " + made.status);
  await page.reload();
  await page.locator(".studio-job .badge", { hasText: "готово" }).first().waitFor({ timeout: 30000 });
  const ta = page.getByTestId("to-app").first();
  await ta.getByText("версией «Кресло NAP 5.1»").waitFor();
  await ta.getByRole("button", { name: "Отправить в приложение" }).click();
  await ta.getByRole("link", { name: "Открыть приложение" }).click();
  await page.waitForURL("**/developer/apps/**");
  const qa = page.getByTestId("layout-qa").first();
  await qa.waitFor({ timeout: 60000 });
  await qa.getByText("Из проекта студии").first().waitFor();
  await qa.getByText("пройдена при экспорте в студии").waitFor();
  await page.screenshot({ path: `${OUT}/studio-8-to-app.png`, fullPage: true });
  // WCAG 2.1 AA on the populated editor (serious/critical issues fail the run, as in a11y.cjs)
  await page.addScriptTag({ content: fs.readFileSync(require.resolve("axe-core/axe.min.js", { paths: [process.cwd()] }), "utf8") });
  const axe = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    resultTypes: ["violations"] })).violations.filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`));
  if (axe.length) throw new Error("a11y: " + axe.join("; "));
  // NAP-101: a custom output profile (2 ms delay on the surrounds, FR polarity) saves as version 1
  await page.goto(`${BASE}/ru/developer/studio`);
  await page.getByRole("heading", { name: "Профили вывода" }).waitFor();
  await page.getByLabel("Новый профиль на основе").selectOption({ label: "Кресло NAP 5.1" });
  await page.getByLabel("SL: Задержка, мс").fill("2");
  await page.getByLabel("SR: Задержка, мс").fill("2");
  await page.getByLabel("FR: Ø полярность").check();
  await page.getByRole("button", { name: "Создать профиль" }).click();
  await page.getByText("версия 1").waitFor();
  await page.screenshot({ path: `${OUT}/studio-7-router.png`, fullPage: true });
  // mobile
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/studio-6-mobile.png`, fullPage: false });
  const must = ["FL FR FC LFE SL SR", "SL\t-18 dBFS", "SR\t-18 dBFS", "FL\tтишина", "LFE\tтишина"];
  const missing = must.filter((x) => !report.includes(x));
  if (!/пройдена/.test(report) || missing.length || errs.length) throw new Error(`report: missing ${missing}; errors ${errs}`);
  console.log(JSON.stringify({ studio: "passed", preview_position: tc }));
  await browser.close();
})().catch((e) => { console.error("FAIL", e.message); process.exit(1); });
