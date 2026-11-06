// NAP-064: subscription tiers. The author makes two plans in the price tab (one opens a single session, the other
// the whole program); a buyer sees both in the store, subscribes to the cheaper one and the player offers only its
// session. The plans are removed at the end so other runs see the demo app unchanged. Chromium, RU.
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://localhost:3000";
const ru = require("../messages/ru.json");
const t = (k, v = {}) => Object.entries(v).reduce((s, [a, b]) => s.replace(`{${a}}`, b), k.split(".").reduce((o, x) => o[x], ru));
const SLUG = "quiet-harbor";

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const page = async () => { const ctx = await browser.newContext(); const p = await ctx.newPage(); p.on("pageerror", (e) => errors.push(String(e))); return { ctx, p }; };

  const a = await page();
  await a.p.goto(`${BASE}/ru/login`);
  await a.p.getByLabel(t("auth.email")).fill("dev1@nap.demo");
  await a.p.getByLabel(t("auth.password")).fill(process.env.DEMO_PASSWORD || "demo-password-1");
  await a.p.getByRole("button", { name: t("auth.login") }).last().click();
  await a.p.waitForURL((u) => !u.pathname.endsWith("/login"));
  const apps = await a.p.evaluate(async () => (await fetch("/api/v1/developer/apps", { credentials: "include" })).json());
  const app = apps.items.find((x) => x.slug === SLUG);
  const detail = await a.p.evaluate(async (id) => (await fetch(`/api/v1/developer/apps/${id}`, { credentials: "include" })).json(), app.id);
  const sessions = detail.versions[0].sessions;
  const firstTitle = sessions[0].titles.ru;

  await a.p.goto(`${BASE}/ru/developer/apps/${app.id}?tab=price`);
  const plan = async (ru, en, amount, onlyFirst) => {
    await a.p.getByLabel(t("dev.price_kind")).selectOption("subscription");
    await a.p.getByLabel(t("account.amount")).fill(String(amount));
    const ed = a.p.getByTestId("tier-editor");
    await ed.getByLabel(t("tier.name_ru")).fill(ru);
    await ed.getByLabel(t("tier.name_en")).fill(en);
    if (onlyFirst) {
      await ed.getByLabel(t("tier.all_sessions")).uncheck();
      for (const s of sessions.slice(1)) await ed.getByLabel(s.titles.ru).uncheck();
    }
    await a.p.getByRole("button", { name: t("common.save") }).click();
    await a.p.getByTestId("tier-list").getByText(ru, { exact: true }).waitFor();
  };
  await plan("Базовый", "Basic", 3, true);
  await plan("Полный", "Full", 9, false);

  const b = await page();
  await b.p.goto(`${BASE}/ru`);
  const email = `tiers${Date.now()}@example.com`;
  const reg = await b.p.request.post(`${BASE}/api/v1/auth/register`, { data: { email, password: "long-password-123", display_name: "Tiers",
    accept_terms: true, consent_personal_data: true, locale: "ru" } });
  if (!reg.ok()) throw new Error(`register ${reg.status()}`);
  await b.p.goto(`${BASE}/ru/apps/${SLUG}`);
  await b.p.getByTestId("tier-of-session").first().waitFor();          // sessions say which plans include them
  await b.p.getByRole("button", { name: /Полный/ }).waitFor();
  await b.p.getByRole("button", { name: /Базовый/ }).click();
  await b.p.waitForURL("**/mock-checkout/**");
  await b.p.getByRole("button", { name: t("mock.pay") }).click();
  await b.p.getByText(t("mock.result_succeeded")).waitFor();
  await b.p.goto(`${BASE}/ru/apps/${SLUG}`);
  await b.p.getByTestId("my-tier").getByText(t("tier.mine", { name: "Базовый" })).waitFor();
  await b.p.goto(`${BASE}/ru/play/${SLUG}`);
  await b.p.getByTestId("tier-locked").waitFor();
  const opts = await b.p.getByLabel(t("player.session")).locator("option").evaluateAll((os) => os.map((o) => [o.textContent, o.disabled]));
  if (!opts[0][0].startsWith(firstTitle) || opts[0][1] || !opts.slice(1).every((o) => o[1])) throw new Error(`player options: ${JSON.stringify(opts)}`);

  for (const tier of ["bazovyi", "basic", "full", "polnyi"]) {
    await a.p.evaluate(async ([id, tier]) => {
      const csrf = document.cookie.match(/nap_csrf=([^;]+)/)?.[1] ?? "";
      await fetch(`/api/v1/developer/apps/${id}/price/tiers/${tier}`, { method: "DELETE", credentials: "include", headers: { "x-csrf-token": decodeURIComponent(csrf) } });
    }, [app.id, tier]);
  }
  await a.p.goto(`${BASE}/ru/developer/apps/${app.id}?tab=price`);
  await a.p.getByLabel(t("dev.price_kind")).waitFor();
  if (await a.p.getByTestId("tier-list").count()) throw new Error("plans were not removed");
  if (errors.length) throw new Error(errors.join("\n"));
  await browser.close();
  console.log(JSON.stringify({ tiers: "passed" }));
})().catch((e) => { console.error(e); process.exit(1); });
