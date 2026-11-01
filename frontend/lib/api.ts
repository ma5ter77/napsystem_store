"use client";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string,
              public details?: Record<string, any>) {
    super(message);
  }
}

function csrf(): string {
  const m = document.cookie.match(/(?:^|; )nap_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function locale(): string {
  const seg = window.location.pathname.split("/")[1];
  return seg === "en" ? "en" : "ru";
}

export async function api<T = any>(path: string, init: { method?: string; body?: unknown; headers?: Record<string, string> } = {}): Promise<T> {
  const method = init.method ?? (init.body !== undefined ? "POST" : "GET");
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        "x-locale": locale(),
        ...(init.body !== undefined ? { "content-type": "application/json" } : {}),
        ...(method !== "GET" ? { "x-csrf-token": csrf() } : {}),
        ...(init.headers ?? {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network_error", locale() === "ru" ? "Нет соединения с сервером." : "No connection to the server.");
  }
  const text = await res.text();
  let json: any = {};
  try { json = text ? JSON.parse(text) : {}; } catch { /* non-json */ }
  if (!res.ok) {
    const e = json.error ?? {};
    throw new ApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.request_id, e.details);
  }
  return json as T;
}

/** NAP-047: send one small file (≤ 10 MB) as the raw request body, e.g. a product photo. */
export async function postFile<T = any>(path: string, file: Blob): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, { method: "POST", credentials: "same-origin", body: file,
      headers: { "x-locale": locale(), "content-type": "application/octet-stream", "x-csrf-token": csrf() } });
  } catch {
    throw new ApiError(0, "network_error", locale() === "ru" ? "Нет соединения с сервером." : "No connection to the server.");
  }
  const text = await res.text();
  let json: any = {};
  try { json = text ? JSON.parse(text) : {}; } catch { /* non-json */ }
  if (!res.ok) {
    const e = json.error ?? {};
    throw new ApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.request_id, e.details);
  }
  return json as T;
}

/** Upload chunk size: below the 10 MB request-body limit of the Next.js proxy and typical CDN limits (NAP-069). */
export const UPLOAD_CHUNK = 8 * 1024 * 1024;

function putChunk(url: string, blob: Blob, first: number, total: number, onLoaded: (n: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.setRequestHeader("content-range", `bytes ${first}-${first + blob.size - 1}/${total}`);
    xhr.setRequestHeader("x-locale", locale());
    xhr.upload.onprogress = (e) => onLoaded(e.loaded);
    xhr.onload = () => {
      let json: any = {};
      try { json = JSON.parse(xhr.responseText); } catch { /* */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(json);
      else reject(new ApiError(xhr.status, json.error?.code ?? "upload_failed", json.error?.message ?? "Upload failed",
                               json.error?.request_id, json.error?.details));
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error"));
    xhr.send(blob);
  });
}

/** Uploads a file in ordered chunks with resume: a lost or repeated chunk is re-synced from the server offset. */
export async function uploadWithProgress(url: string, file: Blob, onProgress: (p: number) => void): Promise<any> {
  const total = file.size;
  let offset = 0, retries = 0, resyncs = 0, last: any = {};
  while (offset < total) {
    const end = Math.min(offset + UPLOAD_CHUNK, total);
    const start = offset;
    try {
      last = await putChunk(url, file.slice(start, end), start, total, (n) => onProgress(Math.min(1, (start + n) / total)));
      offset = end; retries = 0; resyncs = 0;
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409 && typeof e.details?.offset === "number" && ++resyncs <= 5) {
        offset = e.details.offset; continue;
      }
      if (e instanceof ApiError && e.status === 429 && ++retries <= 4) { // NAP-052: wait as the server asks
        await new Promise((r) => setTimeout(r, 1000 * Math.min(60, Number(e.details?.retry_after) || 5))); continue;
      }
      if (e instanceof ApiError && (e.status === 0 || e.status === 502 || e.status === 503 || e.status === 504) && ++retries <= 4) {
        await new Promise((r) => setTimeout(r, 1000 * retries)); continue;
      }
      throw e;
    }
  }
  onProgress(1);
  return last;
}

/** SHA-256 of a file read in 8 MB slices, so a 1 GB upload never sits in memory at once (NAP-069). */
export async function sha256File(file: Blob, onProgress?: (p: number) => void): Promise<string> {
  const { createSHA256 } = await import("hash-wasm");
  const h = await createSHA256();
  h.init();
  for (let off = 0; off < file.size; off += UPLOAD_CHUNK) {
    h.update(new Uint8Array(await file.slice(off, Math.min(off + UPLOAD_CHUNK, file.size)).arrayBuffer()));
    onProgress?.(Math.min(1, (off + UPLOAD_CHUNK) / file.size));
  }
  return h.digest("hex");
}

export async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function uid(): string {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36);
}
