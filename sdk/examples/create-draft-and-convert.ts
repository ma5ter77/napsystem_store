/**
 * Example: create a draft app and run a REAL conversion through the API.
 *
 *   NAP_BASE_URL=http://localhost:8000 NAP_API_KEY=napk_... node dist/examples/create-draft-and-convert.js
 *
 * The API key is created in the developer cabinet (Keys) with scopes: apps:write, apps:read, assets:write,
 * conversions:write, conversions:read. Keep it on the server.
 */
import { NapClient } from "../src/index.js";

function synthWav(seconds: number, fs = 48000): Uint8Array {
  const n = Math.floor(seconds * fs);
  const buf = Buffer.alloc(44 + n * 4);
  buf.write("RIFF", 0); buf.writeUInt32LE(36 + n * 4, 4); buf.write("WAVE", 8);
  buf.write("fmt ", 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(2, 22);
  buf.writeUInt32LE(fs, 24); buf.writeUInt32LE(fs * 4, 28); buf.writeUInt16LE(4, 32); buf.writeUInt16LE(16, 34);
  buf.write("data", 36); buf.writeUInt32LE(n * 4, 40);
  for (let i = 0; i < n; i++) {
    const t = i / fs;
    const v = Math.round(0.2 * Math.sin(2 * Math.PI * 196 * t) * (0.7 + 0.3 * Math.sin(2 * Math.PI * 0.25 * t)) * 32767);
    buf.writeInt16LE(v, 44 + i * 4);
    buf.writeInt16LE(v, 46 + i * 4);
  }
  return new Uint8Array(buf);
}

async function main() {
  const baseUrl = process.env.NAP_BASE_URL ?? "http://localhost:8000";
  const apiKey = process.env.NAP_API_KEY;
  if (!apiKey) throw new Error("Set NAP_API_KEY (server-side developer key)");
  const nap = new NapClient({ baseUrl, apiKey, locale: "en" });

  const slug = `sdk-demo-${Date.now().toString(36)}`;
  const app = await nap.createApp({
    slug, authorName: "SDK Example", category: "meditation",
    metadata: { en: { title: "SDK demo" }, ru: { title: "Демо SDK" } },
  });
  console.log("draft app", app.slug, app.id, "version", app.version_id);

  const asset = await nap.uploadAsset(app.id, "sdk-demo.wav", synthWav(4));
  console.log("uploaded", asset.asset_id, asset.sha256, asset.probe);

  const profiles = await nap.listProfiles();
  const alpha = profiles.items.find((p) => p.slug === "alpha-demo-v1")!;
  const job = await nap.createConversion({ appId: app.id, sourceAssetId: asset.asset_id, profileId: alpha.id,
                                           idempotencyKey: `sdk-${slug}` });
  const done = await nap.waitForConversion(job.id, { onProgress: (c) => console.log(" ", c.state, c.stage_progress) });
  if (done.state !== "completed") throw new Error(`conversion ${done.state}: ${done.error_code}`);
  const layer = done.report!.layer;
  console.log("completed:", {
    left_hz: layer.left_hz, right_hz: layer.right_hz, beat_hz: layer.beat_hz,
    true_peak_dbtp: done.report!.levels.true_peak_master_dbtp,
    master_sha256: done.params!.output.master_sha256,
  });
  const ver = await nap.setProgram(app.id, app.version_id,
    [{ key: "intro", titles: { en: "Intro", ru: "Вступление" }, conversionJobId: done.id }]);
  console.log("program set:", ver.sessions.length, "session(s); draft stays a draft until you submit it.");
  const compatible = await nap.getCompatibleHardware("quiet-harbor");
  console.log("compatible kits for quiet-harbor:", compatible.items.filter((i) => i.covers_requirements).map((i) => i.slug));
}

main().catch((e) => { console.error(e); process.exit(1); });
