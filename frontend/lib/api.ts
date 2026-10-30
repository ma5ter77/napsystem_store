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

export function uploadWithProgress(url: string, file: Blob, onProgress: (p: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("content-type", "application/octet-stream");
    xhr.setRequestHeader("x-locale", locale());
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let json: any = {};
      try { json = JSON.parse(xhr.responseText); } catch { /* */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(json);
      else reject(new ApiError(xhr.status, json.error?.code ?? "upload_failed", json.error?.message ?? "Upload failed",
                               json.error?.request_id, json.error?.details));
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error"));
    xhr.send(file);
  });
}

export async function sha256Hex(buf: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function uid(): string {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36);
}
