// NAP-104 (spec v2.1, M5): reading protected .napx packages in the player with WebCrypto.
// The format and the lease are described in backend/app/secure/napx.py; this file mirrors it byte for byte.
// Works in the browser and in Node ≥ 22 (globalThis.crypto.subtle), no dependencies.
//
//   package = "NAPX" | v1 | 3 zero bytes | u32 manifest length | manifest JSON | u32 64 | Ed25519 signature | segments
//   segment i = AES-256-GCM(plaintext, key = CEK, iv = nonce_prefix ‖ u32 i,
//                           aad = SHA-256(manifest) ‖ u32 i ‖ u8 last)
//   lease  = compact JWS (EdDSA); payload.key = CEK encrypted to this device's P-256 key (ECDH-ES + HKDF-SHA256 + A256GCM)
//
// Honest limit: this keeps copies useless outside this device and detects any change to the file; it is not absolute
// copy protection (the audio has to be decrypted to be played) and offline expiry relies on the device clock.

type Bytes = Uint8Array<ArrayBuffer>;

export type NapxManifest = {
  format: "napx"; v: 1; content_id: string; sha256: string; size: number; content_type: string;
  segment_size: number; segments: number; cipher: "AES-256-GCM"; nonce_prefix: string; key_id: string;
};
export type NapxHeader = { manifest: NapxManifest; manifestBytes: Bytes; signature: Bytes; dataOffset: number };
export type SealedKey = { alg: string; crv: "P-256"; epk: string; iv: string; ct: string };
export type LeasePayload = {
  v: 1; lid: string; sub: string; dev: string; app: string; ver: string; session: string; layout: string;
  content_id: string; sha256: string; manifest_sha256: string; iat: number; exp: number; key: SealedKey;
};
export type SigningKey = { kid: string; alg: "EdDSA"; crv: "Ed25519"; x: string };

export class NapxError extends Error {
  readonly code: string;
  constructor(code: string) { super(code); this.code = code; }
}

const TAG = 16;
const subtle = () => globalThis.crypto.subtle;
const enc = new TextEncoder();

export function b64u(s: string): Bytes {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function toB64(b: Uint8Array): string {
  let s = "";
  for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000));
  return btoa(s);
}

export function hex(b: ArrayBuffer | Uint8Array): string {
  return Array.from(b instanceof Uint8Array ? b : new Uint8Array(b), (x) => x.toString(16).padStart(2, "0")).join("");
}

function u32(n: number): Bytes {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setUint32(0, n);
  return b;
}

function concat(...parts: Uint8Array[]): Bytes {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

async function bytesOf(blob: Blob, start: number, end: number): Promise<Bytes> {
  return new Uint8Array(await blob.slice(start, end).arrayBuffer());
}

/** Reads the header of a package kept as a Blob (Cache Storage / OPFS / download). */
export async function readHeader(blob: Blob): Promise<NapxHeader> {
  const head = await bytesOf(blob, 0, 12);
  if (head.length < 12 || head[0] !== 0x4e || head[1] !== 0x41 || head[2] !== 0x50 || head[3] !== 0x58) throw new NapxError("not_napx");
  if (head[4] !== 1) throw new NapxError("unsupported_version");
  const n = new DataView(head.buffer).getUint32(8);
  if (n <= 0 || n > 64 * 1024) throw new NapxError("bad_manifest");
  const manifestBytes = await bytesOf(blob, 12, 12 + n);
  const sn = new DataView((await bytesOf(blob, 12 + n, 16 + n)).buffer).getUint32(0);
  if (sn !== 64) throw new NapxError("bad_signature");
  const signature = await bytesOf(blob, 16 + n, 16 + n + sn);
  let manifest: NapxManifest;
  try { manifest = JSON.parse(new TextDecoder().decode(manifestBytes)); } catch { throw new NapxError("bad_manifest"); }
  return { manifest, manifestBytes, signature, dataOffset: 16 + n + sn };
}

/** Expected size of the whole package, used to notice a cut-off download before playing. */
export function packageSize(h: NapxHeader): number {
  return h.dataOffset + h.manifest.size + h.manifest.segments * TAG;
}

async function ed25519(key: SigningKey): Promise<CryptoKey | null> {
  try {
    return await subtle().importKey("raw", b64u(key.x), { name: "Ed25519" }, false, ["verify"]);
  } catch {
    return null;   // older browsers without Ed25519 in WebCrypto: the AES-GCM binding still detects changes
  }
}

/** Checks the manifest signature. Returns false only when the browser cannot check Ed25519 at all. */
export async function verifyManifest(h: NapxHeader, key: SigningKey): Promise<boolean> {
  const k = await ed25519(key);
  if (!k) return false;
  if (!(await subtle().verify({ name: "Ed25519" }, k, h.signature, h.manifestBytes))) throw new NapxError("bad_signature");
  return true;
}

/** Decodes the lease, checks its signature (where the browser can) and its expiry against the device clock. */
export async function readLease(token: string, key: SigningKey, now = Date.now() / 1000): Promise<{ payload: LeasePayload; verified: boolean }> {
  const parts = token.split(".");
  if (parts.length !== 3) throw new NapxError("bad_lease");
  let payload: LeasePayload;
  try { payload = JSON.parse(new TextDecoder().decode(b64u(parts[1]))); } catch { throw new NapxError("bad_lease"); }
  const k = await ed25519(key);
  let verified = false;
  if (k) {
    if (!(await subtle().verify({ name: "Ed25519" }, k, b64u(parts[2]), enc.encode(`${parts[0]}.${parts[1]}`)))) throw new NapxError("bad_lease");
    verified = true;
  }
  if (now >= payload.exp) throw new NapxError("lease_expired");
  return { payload, verified };
}

/** Unseals the content key with this device's private key (non-extractable). Another device gets "wrong_device". */
export async function openSealed(devicePrivate: CryptoKey, sealed: SealedKey, leaseId: string): Promise<CryptoKey> {
  try {
    const epk = await subtle().importKey("raw", b64u(sealed.epk), { name: "ECDH", namedCurve: "P-256" }, false, []);
    const shared = await subtle().deriveBits({ name: "ECDH", public: epk }, devicePrivate, 256);
    const hk = await subtle().importKey("raw", shared, "HKDF", false, ["deriveKey"]);
    const kek = await subtle().deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0), info: enc.encode(`nap-lease-v1|${leaseId}`) },
      hk, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const raw = await subtle().decrypt({ name: "AES-GCM", iv: b64u(sealed.iv), additionalData: enc.encode(leaseId) }, kek, b64u(sealed.ct));
    return await subtle().importKey("raw", raw, "AES-GCM", false, ["decrypt"]);
  } catch {
    throw new NapxError("wrong_device");
  }
}

/** Decrypts segment by segment (1 MiB each): only one segment of ciphertext is read from the Blob at a time. */
export async function* decryptSegments(blob: Blob, h: NapxHeader, cek: CryptoKey): AsyncGenerator<Bytes> {
  const m = h.manifest;
  if (blob.size !== packageSize(h)) throw new NapxError(blob.size < packageSize(h) ? "truncated" : "trailing_data");
  const mh = new Uint8Array(await subtle().digest("SHA-256", h.manifestBytes));
  const prefix = b64u(m.nonce_prefix);
  let at = h.dataOffset;
  for (let i = 0; i < m.segments; i++) {
    const plain = Math.min(m.segment_size, m.size - i * m.segment_size);
    const ct = await bytesOf(blob, at, at + plain + TAG);
    at += plain + TAG;
    const last = i === m.segments - 1 ? 1 : 0;
    try {
      yield new Uint8Array(await subtle().decrypt(
        { name: "AES-GCM", iv: concat(prefix, u32(i)), additionalData: concat(mh, u32(i), new Uint8Array([last])) }, cek, ct));
    } catch {
      throw new NapxError("tampered");
    }
  }
}

/** Manifest hash as the lease names it — the lease and the package must belong together. */
export async function manifestSha256(h: NapxHeader): Promise<string> {
  return hex(await subtle().digest("SHA-256", h.manifestBytes));
}

/** Creates the device key pair: the private half cannot be exported, the public half (SPKI, base64) is registered. */
export async function newDeviceKey(): Promise<{ pair: CryptoKeyPair; spki: string }> {
  const pair = await subtle().generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]) as CryptoKeyPair;
  return { pair, spki: toB64(new Uint8Array(await subtle().exportKey("spki", pair.publicKey))) };
}
