// QA sweep: every page for every role, RU/EN, desktop and phone width. Reports console errors, HTTP >= 500,
// failed API calls, horizontal overflow, raw i18n keys and "undefined/NaN/null" leaking into text.
const { chromium } = require(require.resolve("playwright", { paths: [process.cwd()] }));
const S = process.env.SHOTS || "/tmp/nap-sweep";
require("fs").mkdirSync(S, { recursive: true });
const BASE = process.env.BASE || "http://localhost:3000";
const PUBLIC = ["", "/apps", "/apps?sort=rating", "/apps/quiet-harbor", "/apps/night-stories", "/apps/focus-course",
  "/devices", "/devices/nap-studio-headphones", "/kits", "/kits/nap-starter-kit?app=quiet-harbor", "/cart", "/about",
  "/about/changelog", "/about/project", "/login", "/register", "/apps/does-not-exist"];
const ROLES = {
  "user@nap.demo": ["/library", "/orders", "/play/quiet-harbor", ...["purchases", "subscriptions", "devices", "history", "privacy", "notifications"].map((x) => `/account?tab=${x}`)],
  "dev1@nap.demo": ["apps", "reviews", "finance", "analytics", "keys"].map((x) => `/developer?tab=${x}`),
  "seller@nap.demo": ["/seller?tab=orders", "/seller?tab=inventory"],
  "moderator@nap.demo": ["/moderation"],
  "admin@nap.demo": ["users", "profiles", "finance", "orders", "audit", "ops"].map((x) => `/admin?tab=${x}`),
};
const RAW_KEY = /\b(?:nav|app|dev|rev|cart|catalog|common|hw|kit|player|account|admin|mod|seller|about|fields|errors|auth|keys|an|qa|caps|compat)\.[a-z_]+(?:\.[a-z_]+)?\b/;
const issues = [];
async function visit(p, url, tag) {
  const errs = [], bad = [];
  const onC = (m) => { if (m.type() === "error" && !/TUNNEL|fonts|401|status of 404/.test(m.text())) errs.push(m.text().slice(0, 160)); };
  const onR = (r) => { const u = r.url(); if (u.includes("/api/v1/") && r.status() >= 500) bad.push(`${r.status()} ${u.replace(BASE, "")}`); };
  p.on("console", onC); p.on("response", onR);
  try {
    const resp = await p.goto(BASE + url, { waitUntil: "networkidle", timeout: 30000 });
    await p.waitForTimeout(400);
    const st = resp ? resp.status() : 0;
    const info = await p.evaluate((re) => {
      const text = document.querySelector("main")?.innerText ?? document.body.innerText;
      const m = text.match(new RegExp(re));
      const leak = text.match(/\b(undefined|NaN|\[object Object\])\b/);
      // RU pages: interface sentences left in English (author content marked with lang="en" is skipped)
      const eng = document.documentElement.lang === "ru" ? [...document.querySelectorAll("main p, main .hint, main label, main button, main th, main h1, main h2, main h3, main dt, main .notice")]
        .filter((e) => !e.closest("[lang=en]") && /^[^А-Яа-яЁё]*$/.test(e.innerText) && (e.innerText.match(/\b[A-Za-z]{3,}\b/g) || []).length >= 4)
        .map((e) => e.innerText.slice(0, 70)) : [];
      return { over: document.documentElement.scrollWidth - innerWidth, key: m && m[0], leak: leak && leak[0], eng: eng.slice(0, 3) };
    }, RAW_KEY.source);
    if (st >= 500) issues.push(`${tag} ${url}: page HTTP ${st}`);
    if (info.over > 1) issues.push(`${tag} ${url}: horizontal overflow ${info.over}px`);
    if (info.key && !url.includes("tab=audit")) issues.push(`${tag} ${url}: raw i18n key "${info.key}"`);
    for (const e of info.eng) issues.push(`${tag} ${url}: English text on RU page "${e}"`);
    if (info.leak) issues.push(`${tag} ${url}: text shows "${info.leak}"`);
    for (const e of errs) issues.push(`${tag} ${url}: console ${e}`);
    for (const b of bad) issues.push(`${tag} ${url}: API ${b}`);
  } catch (e) { issues.push(`${tag} ${url}: ${e.message.split("\n")[0]}`); }
  p.off("console", onC); p.off("response", onR);
}
(async () => {
  const b = await chromium.launch();
  let n = 0;
  for (const L of ["ru", "en"]) for (const vp of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const tag = `[${L} ${vp.width}]`;
    const ctx = await b.newContext({ viewport: vp, locale: L }); const p = await ctx.newPage();
    for (const u of PUBLIC) { await visit(p, `/${L}${u}`, tag + "[guest]"); n++; }
    await ctx.close();
    for (const [email, pages] of Object.entries(ROLES)) {
      const c = await b.newContext({ viewport: vp, locale: L }); const q = await c.newPage();
      await q.goto(`${BASE}/${L}/login`); await q.getByLabel("E-mail").fill(email);
      await q.locator("input[type=password]").fill("demo-password-1"); await q.locator("form button").first().click();
      await q.waitForURL((x) => !x.pathname.endsWith("/login"));
      for (const u of pages) { await visit(q, `/${L}${u}`, `${tag}[${email.split("@")[0]}]`); n++; }
      // first item of list pages: order detail, moderation version, seller order, developer app editor tabs
      const firstLinks = { "user@nap.demo": `/${L}/orders`, "moderator@nap.demo": `/${L}/moderation`, "seller@nap.demo": `/${L}/seller`, "dev1@nap.demo": `/${L}/developer?tab=apps` };
      if (firstLinks[email]) {
        await q.goto(BASE + firstLinks[email], { waitUntil: "networkidle" });
        const href = await q.locator('main a[href*="/orders/"], main a[href*="/moderation/"], main a[href*="/developer/apps/"]').first().getAttribute("href").catch(() => null);
        if (href) {
          if (href.includes("/developer/apps/")) for (const tab of ["details", "audio", "program", "price", "versions"]) { await visit(q, `${href}?tab=${tab}`, `${tag}[${email.split("@")[0]}]`); n++; }
          else { await visit(q, href, `${tag}[${email.split("@")[0]}]`); n++; }
        } else issues.push(`${tag} ${firstLinks[email]}: no detail link found`);
      }
      await c.close();
    }
  }
  await b.close();
  console.log(`visited ${n} pages, ${issues.length} issues`);
  console.log([...new Set(issues)].join("\n"));
})();
