// NAP-043: accessibility audit (WCAG 2.1 A/AA) — axe-core on every page for every role, RU and EN,
// plus keyboard checks axe cannot do: skip link first in tab order, visible focus, no keyboard traps in the header.
// Usage: node e2e/a11y.cjs [--json out.json]   (BASE=http://localhost:3000)
const { chromium } = require(require.resolve("playwright", { paths: [process.cwd()] }));
const fs = require("fs");
const path = require("path");
const BASE = process.env.BASE || "http://localhost:3000";
const AXE = fs.readFileSync(require.resolve("axe-core/axe.min.js", { paths: [process.cwd()] }), "utf8");
const PUBLIC = ["", "/apps", "/apps/quiet-harbor", "/devices", "/devices/nap-studio-headphones", "/kits",
  "/kits/nap-starter-kit?app=quiet-harbor", "/cart", "/about", "/login", "/register"];
const ROLES = {
  "user@nap.demo": ["/library", "/orders", "/play/quiet-harbor", "/account?tab=purchases", "/account?tab=notifications", "/account?tab=privacy"],
  "dev1@nap.demo": ["/developer?tab=apps", "/developer?tab=reviews", "/developer?tab=finance"],
  "seller@nap.demo": ["/seller?tab=orders", "/seller?tab=products", "/seller?tab=inventory"],
  "moderator@nap.demo": ["/moderation", "/moderation?tab=apps", "/moderation?tab=products", "/moderation?tab=reviews"],
  "admin@nap.demo": ["/admin?tab=users", "/admin?tab=ops", "/content", "/content?tab=categories"],
};
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const found = new Map(); // rule id -> {impact, help, pages:Set, nodes:[...]}
const kb = [];

async function audit(p, url, who) {
  await p.goto(BASE + url, { waitUntil: "networkidle", timeout: 30000 });
  await p.waitForTimeout(500);
  await p.addScriptTag({ content: AXE });
  const res = await p.evaluate(async (tags) => {
    const r = await window.axe.run(document, { runOnly: { type: "tag", values: tags }, resultTypes: ["violations"] });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, url: v.helpUrl,
      nodes: v.nodes.slice(0, 4).map((n) => ({ target: n.target.join(" "), summary: (n.failureSummary || "").split("\n").slice(1, 3).join(" ").slice(0, 200), html: n.html.slice(0, 160) })) }));
  }, TAGS);
  for (const v of res) {
    const f = found.get(v.id) || { impact: v.impact, help: v.help, url: v.url, pages: new Set(), nodes: [] };
    f.pages.add(`${who} ${url}`);
    if (f.nodes.length < 6) f.nodes.push(...v.nodes.slice(0, 6 - f.nodes.length).map((n) => ({ ...n, page: url })));
    found.set(v.id, f);
  }
}

async function keyboard(p, url) {
  await p.goto(BASE + url, { waitUntil: "networkidle" });
  await p.keyboard.press("Tab");
  const first = await p.evaluate(() => {
    const a = document.activeElement;
    const cs = a ? getComputedStyle(a) : null;
    return { tag: a?.tagName, text: (a?.innerText || a?.getAttribute("aria-label") || "").trim().slice(0, 40), href: a?.getAttribute("href"),
      visible: !!a && a.getBoundingClientRect().height > 0 && cs.visibility !== "hidden",
      outline: cs ? (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none" : false };
  });
  if (!first.href || !first.href.startsWith("#")) kb.push(`${url}: first Tab goes to "${first.text}" (${first.tag}) — no skip link to main content`);
  if (!first.outline) kb.push(`${url}: focused element "${first.text}" has no visible focus indicator`);
  // walk 40 tab stops: every focused element must show a focus indicator and the focus must move
  let last = "", same = 0, noRing = [];
  for (let i = 0; i < 40; i++) {
    await p.keyboard.press("Tab");
    const s = await p.evaluate(() => {
      const a = document.activeElement; if (!a || a === document.body) return null;
      const cs = getComputedStyle(a);
      const ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none";
      return { id: a.outerHTML.slice(0, 80), ring, label: (a.innerText || a.getAttribute("aria-label") || a.getAttribute("name") || "").trim().slice(0, 30) };
    });
    if (!s) break;
    if (!s.ring) noRing.push(s.label || s.id.slice(0, 40));
    same = s.id === last ? same + 1 : 0; last = s.id;
    if (same > 2) { kb.push(`${url}: focus stuck on ${s.label}`); break; }
  }
  if (noRing.length) kb.push(`${url}: no visible focus on ${noRing.length} of 40 tab stops, e.g. ${[...new Set(noRing)].slice(0, 4).join(" | ")}`);
}

(async () => {
  const b = await chromium.launch();
  let n = 0;
  for (const L of ["ru", "en"]) {
    const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: L });
    const p = await ctx.newPage();
    for (const u of PUBLIC) { await audit(p, `/${L}${u}`, "guest"); n++; }
    if (L === "ru") for (const u of ["", "/apps", "/apps/quiet-harbor", "/login"]) await keyboard(p, `/ru${u}`);
    await ctx.close();
    for (const [email, pages] of Object.entries(ROLES)) {
      const c = await b.newContext({ viewport: { width: 1280, height: 900 }, locale: L });
      const q = await c.newPage();
      await q.goto(`${BASE}/${L}/login`);
      await q.locator("input[type=email]").fill(email);
      await q.locator("input[type=password]").fill("demo-password-1");
      await q.locator("form button[type=submit], form button").first().click();
      await q.waitForURL((x) => !x.pathname.endsWith("/login"));
      for (const u of pages) { await audit(q, `/${L}${u}`, email.split("@")[0]); n++; }
      await c.close();
    }
  }
  await b.close();
  const order = { critical: 0, serious: 1, moderate: 2, minor: 3 };
  const rows = [...found.entries()].sort((a, c) => order[a[1].impact] - order[c[1].impact] || c[1].pages.size - a[1].pages.size);
  console.log(`pages audited: ${n}; rules violated: ${rows.length}`);
  for (const [id, f] of rows) {
    console.log(`\n[${f.impact}] ${id} — ${f.help} — ${f.pages.size} page(s)`);
    for (const nd of f.nodes.slice(0, 3)) console.log(`   ${nd.page}  ${nd.target}  ${nd.summary}`);
  }
  console.log(`\nkeyboard: ${kb.length ? "" : "ok"}`); kb.forEach((x) => console.log("  " + x));
  const out = process.argv.indexOf("--json");
  if (out > 0) fs.writeFileSync(process.argv[out + 1], JSON.stringify({ pages: n, rules: rows.map(([id, f]) => ({ id, impact: f.impact, help: f.help, url: f.url, pages: [...f.pages], nodes: f.nodes })), keyboard: kb }, null, 1));
  process.exit(rows.some(([, f]) => f.impact === "critical" || f.impact === "serious") || kb.length ? 1 : 0);
})().catch((e) => { console.error("ERR", e.message.split("\n")[0]); process.exit(2); });
