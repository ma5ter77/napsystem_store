// Every HTML `pattern` in the UI must compile with the `v` flag: browsers build it as new RegExp(`^(?:${p})$`, "v")
// and silently skip validation when it does not compile (NAP-067). Checks string literals and pattern constants.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const files = [];
const walk = (d) => readdirSync(d).forEach((f) => { const p = join(d, f); statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(f) && files.push(p); });
["app", "components", "lib"].forEach(walk);
const found = [];
for (const f of files) {
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(/\bpattern="([^"]*)"/g)) found.push([f, m[1]]);
  // JS string forms: pattern={"..."} and exported *_PATTERN = "..." constants (unescape the JS literal)
  for (const m of src.matchAll(/(?:\bpattern=\{|_PATTERN\s*=\s*)"((?:[^"\\]|\\.)*)"/g)) found.push([f, JSON.parse(`"${m[1]}"`)]);
}
const bad = [];
for (const [f, p] of found) {
  try { new RegExp(`^(?:${p})$`, "v"); } catch (e) { bad.push(`${f}: pattern ${p} — ${e.message}`); }
}
if (bad.length) { console.error(bad.join("\n")); process.exit(1); }
console.log(`patterns ok: ${found.length} checked with the v flag`);
