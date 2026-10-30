// Fails when a dictionary key is missing in one locale, or a key used in code is absent (criterion 38).
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const flat = (o, p = "") => Object.entries(o).flatMap(([k, v]) => (typeof v === "object" ? flat(v, `${p}${k}.`) : [`${p}${k}`]));
const ru = new Set(flat(JSON.parse(readFileSync("messages/ru.json", "utf8"))));
const en = new Set(flat(JSON.parse(readFileSync("messages/en.json", "utf8"))));
const problems = [];
for (const k of ru) if (!en.has(k)) problems.push(`en missing ${k}`);
for (const k of en) if (!ru.has(k)) problems.push(`ru missing ${k}`);
const files = [];
const walk = (d) => readdirSync(d).forEach((f) => { const p = join(d, f); statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(f) && files.push(p); });
["app", "components"].forEach(walk);
let used = 0;
for (const f of files) {
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(/\bt\("([a-z0-9_.]+)"/g)) {
    used++;
    if (!ru.has(m[1])) problems.push(`${f}: unknown key ${m[1]}`);
  }
  for (const m of src.matchAll(/\bt\(`([a-z0-9_]+)\./g)) {
    if (![...ru].some((k) => k.startsWith(m[1] + "."))) problems.push(`${f}: unknown namespace ${m[1]}`);
  }
}
if (problems.length) { console.error(problems.join("\n")); process.exit(1); }
console.log(`i18n ok: ${ru.size} keys per locale, ${used} static usages checked`);
