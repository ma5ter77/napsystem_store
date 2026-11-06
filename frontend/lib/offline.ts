// NAP-104, stage 2: programs saved for listening without the internet.
// The browser keeps only the encrypted .napx package (Cache Storage, on disk) and the lease (IndexedDB). The device's
// P-256 private key is created non-extractable and stays in IndexedDB; the server knows only its public half.
// Playing decrypts the package with WebCrypto into memory for that one session; nothing decrypted is written to disk.
import { api, ApiError } from "./api";
import * as napx from "./napx";

const DB = "nap-secure";
const CACHE = "nap-napx-v1";

export type SavedProgram = {
  lease_id: string; lease: string; expires_at: string; signing_key: napx.SigningKey;
  app: { id: string; slug: string; title: string; version: string };
  session: { key: string; title: string; duration_seconds: number };
  playback: { layout: string; channels: number; channel_order: string[] };
  package: { sha256: string; size_bytes: number; manifest_sha256: string };
  saved_at: string; status?: "active" | "expired" | "revoked"; reason?: string | null;
};
type DeviceRecord = { id: "device"; device_id: string; keys: CryptoKeyPair };

export function supported(): boolean {
  return typeof indexedDB !== "undefined" && typeof caches !== "undefined" && !!globalThis.crypto?.subtle;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => {
      r.result.createObjectStore("meta", { keyPath: "id" });
      r.result.createObjectStore("programs", { keyPath: "lease_id" });
    };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function tx<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.oncomplete = () => { db.close(); resolve(req ? req.result : undefined); };
    t.onerror = () => { db.close(); reject(t.error); };
  });
}

const cacheKey = (leaseId: string) => `/__nap-napx/${leaseId}`;

/** This browser's device key, registered once. A device removed in the profile gets a new key on the next save. */
export async function ensureDevice(name: string): Promise<DeviceRecord> {
  const have = await tx<DeviceRecord>("meta", "readonly", (s) => s.get("device"));
  if (have) return have;
  const { pair, spki } = await napx.newDeviceKey();
  const d = await api<{ id: string }>("/me/license-devices", { body: { public_key: spki, name, platform: "web" } });
  const rec: DeviceRecord = { id: "device", device_id: d.id, keys: pair };
  await tx("meta", "readwrite", (s) => s.put(rec));
  return rec;
}

async function forgetDevice() {
  await tx("meta", "readwrite", (s) => s.delete("device"));
}

export async function list(): Promise<SavedProgram[]> {
  return ((await tx<SavedProgram[]>("programs", "readonly", (s) => s.getAll())) ?? [])
    .sort((a, b) => b.saved_at.localeCompare(a.saved_at));
}

export async function remove(leaseId: string) {
  await tx("programs", "readwrite", (s) => s.delete(leaseId));
  await caches.open(CACHE).then((c) => c.delete(cacheKey(leaseId))).catch(() => {});
}

/** Gets a lease and the encrypted package, checks it and keeps both. Replaces an older copy of the same program. */
export async function save(opts: { appId: string; appTitle: string; sessionKey: string; sessionTitle: string; layout?: string;
  deviceName: string; onProgress?: (share: number) => void; signal?: AbortSignal }): Promise<SavedProgram> {
  let dev = await ensureDevice(opts.deviceName);
  const body = () => ({ device_id: dev.device_id, app_id: opts.appId, session_key: opts.sessionKey, layout: opts.layout ?? "headphones" });
  let out: any;
  try {
    out = await api("/me/leases", { body: body() });
  } catch (e) {
    if (!(e instanceof ApiError) || (e.code !== "license_device_revoked" && e.status !== 404)) throw e;
    await forgetDevice();            // removed in the profile (or another account on this browser): new key
    dev = await ensureDevice(opts.deviceName);
    out = await api("/me/leases", { body: body() });
  }
  const res = await fetch(out.package.url, { credentials: "include", signal: opts.signal });
  if (!res.ok || !res.body) throw new ApiError(res.status, "download_failed", "download_failed");
  const { createSHA256 } = await import("hash-wasm");
  const h = await createSHA256();
  h.init();
  let got = 0;
  const counted = res.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform: (chunk, ctl) => { h.update(chunk); got += chunk.byteLength; opts.onProgress?.(Math.min(1, got / out.package.size_bytes)); ctl.enqueue(chunk); },
  }));
  const cache = await caches.open(CACHE);
  await cache.put(cacheKey(out.lease_id), new Response(counted, { headers: { "Content-Type": "application/octet-stream" } }));
  const blob = await (await cache.match(cacheKey(out.lease_id)))!.blob();
  const header = await napx.readHeader(blob).catch(() => null);
  if (h.digest("hex") !== out.package.sha256 || !header || (await napx.manifestSha256(header)) !== out.package.manifest_sha256) {
    await cache.delete(cacheKey(out.lease_id));
    throw new ApiError(0, "checksum", "checksum");
  }
  for (const old of await list()) if (old.app.id === opts.appId && old.session.key === opts.sessionKey) await remove(old.lease_id);
  const rec: SavedProgram = {
    lease_id: out.lease_id, lease: out.lease, expires_at: out.expires_at, signing_key: out.signing_key,
    app: { id: out.app.id, slug: out.app.slug, title: opts.appTitle, version: out.app.version },
    session: { key: out.program_session.key, title: opts.sessionTitle, duration_seconds: out.program_session.duration_seconds },
    playback: out.playback, package: out.package, saved_at: new Date().toISOString(), status: "active",
  };
  await tx("programs", "readwrite", (s) => s.put(rec));
  return rec;
}

/** When online: asks the server about every saved lease and deletes copies that may no longer be played. */
export async function sync(): Promise<SavedProgram[]> {
  if (typeof navigator !== "undefined" && !navigator.onLine) return list();
  for (const p of await list()) {
    try {
      const st = await api<{ status: SavedProgram["status"]; reason: string | null }>(`/me/leases/${p.lease_id}`);
      if (st.status !== "active") await remove(p.lease_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) await remove(p.lease_id);   // another account or a removed lease
    }
  }
  return list();
}

/** Checks the lease and the package and decrypts the audio for playback. Throws NapxError with a code. */
export async function openForPlayback(p: SavedProgram, onProgress?: (share: number) => void): Promise<Blob> {
  const dev = await tx<DeviceRecord>("meta", "readonly", (s) => s.get("device"));
  if (!dev) throw new napx.NapxError("wrong_device");
  const hit = await (await caches.open(CACHE)).match(cacheKey(p.lease_id));
  if (!hit) throw new napx.NapxError("missing");
  const blob = await hit.blob();
  const { payload } = await napx.readLease(p.lease, p.signing_key);
  if (payload.dev !== dev.device_id) throw new napx.NapxError("wrong_device");
  const header = await napx.readHeader(blob);
  await napx.verifyManifest(header, p.signing_key);
  if ((await napx.manifestSha256(header)) !== payload.manifest_sha256) throw new napx.NapxError("tampered");
  const cek = await napx.openSealed(dev.keys.privateKey, payload.key, payload.lid);
  const parts: Uint8Array<ArrayBuffer>[] = [];
  let i = 0;
  for await (const seg of napx.decryptSegments(blob, header, cek)) {
    parts.push(seg);
    onProgress?.(++i / header.manifest.segments);
  }
  return new Blob(parts, { type: header.manifest.content_type });
}
