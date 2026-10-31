"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { money } from "@/lib/i18n";
import { StatePanel, type StateKind } from "@/components/State";
import { useApp } from "./Providers";

export function Price({ minor, currency }: { minor: number | null | undefined; currency: string }) {
  const { locale } = useApp();
  return <span className="price">{money(minor, currency, locale)}</span>;
}

const FIELD_LOC_SKIP = new Set(["body", "query", "path", "header"]);

/** Field names named by an API error: `details.field` (service checks) or `details.fields`
 *  as strings or as FastAPI items `{loc: ["body", "slug"], type}`. */
export function errorFields(error: unknown): string[] {
  const d = (error as ApiError | null)?.details;
  if (!d) return [];
  const out: string[] = [];
  if (typeof d.field === "string") out.push(d.field);
  if (Array.isArray(d.fields)) for (const f of d.fields) {
    if (typeof f === "string") out.push(f);
    else if (f && Array.isArray(f.loc)) {
      const name = [...f.loc].reverse().find((x: unknown) => typeof x === "string" && !FIELD_LOC_SKIP.has(x) && !/^\d+$/.test(x));
      if (name) out.push(name as string);
    }
  }
  return [...new Set(out)];
}

const UNAVAILABLE = new Set(["device_busy", "device_protocol_unavailable", "incompatible_device", "sensor_unavailable",
  "payment_provider_unavailable", "carrier_timeout", "network_error"]);

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t, locale } = useApp();
  if (!error) return null;
  const e = error as ApiError;
  const label = (f: string) => {
    const m = /^sessions\.(\d+)\.(key|titles|track)$/.exec(f); // program builder: "Session 2: Track"
    if (m) return `${t("dev.session_n", { n: m[1] })}: ${t(`fields.session_${m[2]}`)}`;
    const k = `fields.${f}`; const s = t(k); return s === k ? f : s;
  };
  const fields = errorFields(error).map(label);
  // NAP-044: "something is unavailable" (a device, the payment provider, a carrier) is not the user's mistake:
  // the shared "unavailable" panel says so and offers what to do instead
  if (e.code && UNAVAILABLE.has(e.code)) {
    const device = e.code.startsWith("device") || e.code === "incompatible_device" || e.code === "sensor_unavailable";
    return (
      <StatePanel kind="unavailable" role="alert" title={t(device ? "state.device_title" : "state.provider_title")}
        actions={<>{onRetry && <button className="btn small" onClick={onRetry}>{t("common.retry")}</button>}
          {device && <a className="btn small ghost" href={`/${locale}/account?tab=devices`}>{t("state.my_devices")}</a>}</>}>
        {e.message}{e.requestId ? <span className="hint"> · {t("common.request_id")}: {e.requestId}</span> : null}
      </StatePanel>
    );
  }
  return (
    <div className="notice bad" role="alert">
      <div>{e.message || t("common.error")}</div>
      {fields.length > 0 && <div className="hint">{t("common.fields")}: {fields.join(", ")}</div>}
      {e.details?.reasons && <div className="hint">{e.details.reasons.map((r: string) => t(`reasons.${r.split(":")[0]}`, { cap: r.split(":")[1] ?? "" })).join("; ")}</div>}
      {e.requestId && <div className="hint">{t("common.request_id")}: {e.requestId}</div>}
      {onRetry && <button className="btn small ghost" onClick={onRetry} style={{ marginTop: 8 }}>{t("common.retry")}</button>}
    </div>
  );
}

export function Loading({ lines = 3 }: { lines?: number }) {
  const { t } = useApp();
  return (
    <div className="stack" aria-busy="true" aria-live="polite">
      <span className="sr-only">{t("common.loading")}</span>
      {Array.from({ length: lines }, (_, i) => <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />)}
    </div>
  );
}

/** Empty list / nothing to show — the shared state panel (NAP-044). `kind` changes the icon and tone. */
export function Empty({ children, action, title, kind = "empty" }: { children: React.ReactNode; action?: React.ReactNode; title?: React.ReactNode; kind?: StateKind }) {
  return <StatePanel kind={kind} title={title} actions={action}>{children}</StatePanel>;
}

/** Loads data with loading / error / retry states. */
export function useLoad<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    setError(null);
    try { setData(await api<T>(path)); } catch (e) { setError(e); } finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => { load(); }, [load]);
  return { data, error, loading, reload: load, setData };
}

/** Runs an action, exposing pending + error (for "незавершённая операция"). */
export function useAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    setPending(true);
    setError(null);
    try { return await fn(); } catch (e) { setError(e); return undefined; } finally { setPending(false); }
  }, []);
  return { pending, error, run, setError };
}

export function RequireAuth({ role, children }: { role?: string | string[]; children: React.ReactNode }) {
  const { me, t, locale } = useApp();
  if (me === undefined) return <Loading />;
  if (me === null) {
    return <Empty kind="locked" title={t("state.login_title")} action={<a className="btn" href={`/${locale}/login?next=${encodeURIComponent(typeof window !== "undefined" ? location.pathname + location.search : "/")}`}>{t("nav.login")}</a>}>{t("common.login_required")}</Empty>;
  }
  const roles = Array.isArray(role) ? role : role ? [role] : [];
  if (roles.length && !roles.some((r) => me.roles.includes(r))) return <Empty kind="locked" title={t("state.no_role_title")} action={<a className="btn ghost" href={`/${locale}`}>{t("state.home")}</a>}>{t("common.no_role")}</Empty>;
  return <>{children}</>;
}

/** Form draft kept in sessionStorage (safe fields only) so switching language does not lose input. */
export function useDraft<T extends object>(key: string, initial: T): [T, (v: T) => void, () => void] {
  const [v, setV] = useState<T>(initial);
  const loaded = useRef(false);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(`draft:${key}`);
      if (raw) setV({ ...initial, ...JSON.parse(raw) });
    } catch { /* storage unavailable */ }
    loaded.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const set = (nv: T) => {
    setV(nv);
    try { sessionStorage.setItem(`draft:${key}`, JSON.stringify(nv)); } catch { /* */ }
  };
  const clear = () => { try { sessionStorage.removeItem(`draft:${key}`); } catch { /* */ } };
  return [v, set, clear];
}

export function StatusBadge({ ns, value }: { ns: string; value: string | null | undefined }) {
  const { t } = useApp();
  if (!value) return null;
  const tone = /completed|paid|published|delivered|approved|active|verified|ok|passed|confirmed|shipped|closed/.test(value) ? "ok"
    : /fail|reject|exception|cancel|incompatible|refunded|expired|revoked/.test(value) ? "bad"
    : /pending|queued|awaiting|submitted|processing|vendor|partial|past_due|draft|requested/.test(value) ? "warn" : "gray";
  return <span className={`badge ${tone}`}>{t(`${ns}.${value}`)}</span>;
}

export function TestModeBanner() {
  const { t } = useApp();
  return <div className="notice test" role="note">{t("common.test_mode")}</div>;
}
