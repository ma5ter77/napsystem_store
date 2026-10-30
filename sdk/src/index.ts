/**
 * NAP Store SDK (server-side).
 *
 * Developer API keys (napk_...) are secrets: use them only on your server. The client refuses to run with a
 * developer key inside a browser. Machine error codes and method names never depend on the UI language.
 */
import { createHash } from "node:crypto";

export type Locale = "ru" | "en";

export interface NapClientOptions {
  baseUrl: string;
  apiKey: string;
  locale?: Locale;
  fetch?: typeof fetch;
}

export class NapApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
    public readonly details?: Record<string, unknown>,
  ) {
    super(`${code}: ${message}`);
    this.name = "NapApiError";
  }
}

export type ConversionState =
  | "queued" | "validating" | "decoding" | "rendering" | "quality_check" | "packaging"
  | "completed" | "failed" | "canceled";

export interface Conversion {
  id: string;
  app_id: string;
  state: ConversionState;
  stage_progress: number;
  attempt: number;
  error_code: string | null;
  profile: Record<string, unknown>;
  result: Record<string, unknown> | null;
  report: Record<string, any> | null;
  params: Record<string, any> | null;
  urls?: Record<string, string>;
}

export interface AppMetadata { title: string; subtitle?: string; description?: string }

export interface CreateAppInput {
  slug: string;
  authorName: string;
  metadata: Partial<Record<Locale, AppMetadata>>;
  category?: string;
  contentLanguage?: string;
}

export interface SessionCommandInput {
  commandId: string;
  command: "precheck" | "confirm_ready" | "start" | "pause" | "resume" | "complete" | "stop" | "fail";
  expectedVersion: number;
  data?: Record<string, unknown>;
}

const TERMINAL: ConversionState[] = ["completed", "failed", "canceled"];

export class NapClient {
  private readonly base: string;
  private readonly key: string;
  private readonly locale: Locale;
  private readonly f: typeof fetch;

  constructor(opts: NapClientOptions) {
    if (typeof (globalThis as any).window !== "undefined" && opts.apiKey?.startsWith("napk_")) {
      throw new Error("NAP developer keys are server-side secrets and must not be used in a browser bundle");
    }
    if (!opts.apiKey) throw new Error("apiKey is required");
    this.base = opts.baseUrl.replace(/\/$/, "") + "/api/v1";
    this.key = opts.apiKey;
    this.locale = opts.locale ?? "en";
    this.f = opts.fetch ?? fetch;
  }

  private async req<T>(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<T> {
    const res = await this.f(this.base + path, {
      method,
      headers: {
        authorization: `Bearer ${this.key}`,
        "x-locale": this.locale,
        ...(body !== undefined ? { "content-type": "application/json" } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    const json = text ? JSON.parse(text) : {};
    if (!res.ok) {
      const e = json.error ?? {};
      throw new NapApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.request_id, e.details);
    }
    return json as T;
  }

  // ---------------------------------------------------------------- apps & versions
  createApp(input: CreateAppInput): Promise<{ id: string; slug: string; version_id: string }> {
    return this.req("POST", "/developer/apps", {
      slug: input.slug, author_name: input.authorName, metadata: input.metadata, category: input.category,
      content_language: input.contentLanguage ?? "none",
    });
  }

  listApps(): Promise<{ items: Array<Record<string, any>> }> {
    return this.req("GET", "/developer/apps");
  }

  createVersion(appId: string): Promise<Record<string, any>> {
    return this.req("POST", `/developer/apps/${appId}/versions`);
  }

  updateVersion(appId: string, versionId: string, patch: Record<string, unknown>): Promise<Record<string, any>> {
    return this.req("PATCH", `/developer/apps/${appId}/versions/${versionId}`, patch);
  }

  setProgram(appId: string, versionId: string,
             sessions: Array<{ key: string; titles: Partial<Record<Locale, string>>; conversionJobId: string }>) {
    return this.req<Record<string, any>>("PUT", `/developer/apps/${appId}/versions/${versionId}/program`, {
      sessions: sessions.map((s) => ({ key: s.key, titles: s.titles, conversion_job_id: s.conversionJobId })),
    });
  }

  validateManifest(manifest: Record<string, unknown>): Promise<{ valid: boolean; errors: Array<{ path: string; message: string }> }> {
    return this.req("POST", "/developer/manifest/validate", manifest);
  }

  submitVersion(appId: string, versionId: string): Promise<Record<string, any>> {
    return this.req("POST", `/developer/apps/${appId}/versions/${versionId}/submit`);
  }

  // ---------------------------------------------------------------- assets & conversions
  /** Uploads a source file: requests a single-use upload ticket, then PUTs the bytes with size + SHA-256 check. */
  async uploadAsset(appId: string, filename: string, data: Uint8Array): Promise<{ asset_id: string; sha256: string; probe: any }> {
    const sha256 = createHash("sha256").update(data).digest("hex");
    const ticket = await this.req<{ asset_id: string; upload_url: string }>("POST", `/developer/apps/${appId}/uploads`,
      { filename, size_bytes: data.byteLength, sha256 });
    const res = await this.f(this.base.replace(/\/api\/v1$/, "") + ticket.upload_url, {
      method: "PUT", body: data as unknown as BodyInit, headers: { "content-type": "application/octet-stream", "x-locale": this.locale },
    });
    const json: any = await res.json();
    if (!res.ok) throw new NapApiError(res.status, json.error?.code, json.error?.message, json.error?.request_id);
    return json;
  }

  listProfiles(): Promise<{ items: Array<{ id: string; slug: string; version: number; params: Record<string, number> }> }> {
    return this.req("GET", "/developer/profiles");
  }

  createConversion(input: { appId: string; sourceAssetId: string; profileId: string; idempotencyKey?: string }): Promise<Conversion & { created: boolean }> {
    return this.req("POST", "/developer/conversions",
      { app_id: input.appId, source_asset_id: input.sourceAssetId, profile_id: input.profileId },
      input.idempotencyKey ? { "Idempotency-Key": input.idempotencyKey } : {});
  }

  getConversion(id: string): Promise<Conversion> {
    return this.req("GET", `/developer/conversions/${id}`);
  }

  async waitForConversion(id: string, opts: { timeoutMs?: number; intervalMs?: number; onProgress?: (c: Conversion) => void } = {}): Promise<Conversion> {
    const deadline = Date.now() + (opts.timeoutMs ?? 10 * 60_000);
    for (;;) {
      const c = await this.getConversion(id);
      opts.onProgress?.(c);
      if (TERMINAL.includes(c.state)) return c;
      if (Date.now() > deadline) throw new Error(`conversion ${id} did not finish in time`);
      await new Promise((r) => setTimeout(r, opts.intervalMs ?? 1000));
    }
  }

  // ---------------------------------------------------------------- store & sessions
  getEntitlement(appId: string): Promise<{ has_access: boolean; source: string | null; paid_until: string | null }> {
    return this.req("GET", `/entitlements/${appId}`);
  }

  createSession(input: { appId: string; sessionKey: string; deviceId: string }): Promise<Record<string, any>> {
    return this.req("POST", "/sessions", { app_id: input.appId, session_key: input.sessionKey, device_id: input.deviceId });
  }

  sendSessionCommand(sessionId: string, cmd: SessionCommandInput): Promise<{ state: string; state_version: number; replayed?: boolean }> {
    return this.req("POST", `/sessions/${sessionId}/commands`, {
      command_id: cmd.commandId, command: cmd.command, expected_version: cmd.expectedVersion, data: cmd.data ?? {},
    });
  }

  getDeveloperAnalytics(): Promise<{ items: Array<Record<string, any>> }> {
    return this.req("GET", "/developer/analytics");
  }

  listHardware(query: { category?: string; q?: string; kind?: "devices" | "kits" } = {}): Promise<{ items: any[]; total: number }> {
    const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][]).toString();
    return this.req("GET", `/hardware/products${qs ? "?" + qs : ""}`);
  }

  getCompatibleHardware(appSlug: string): Promise<{ app: string; items: any[] }> {
    return this.req("GET", `/hardware/compatible?app=${encodeURIComponent(appSlug)}`);
  }
}

export default NapClient;
