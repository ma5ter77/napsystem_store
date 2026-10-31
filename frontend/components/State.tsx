// NAP-044: one look and wording for empty lists, errors, "not found" and unavailable things — on server and client
// pages alike (no hooks here, so server components can render it too).
import type { ReactNode } from "react";

export type StateKind = "empty" | "error" | "not_found" | "unavailable" | "locked";
const ICON: Record<StateKind, string> = { empty: "○", error: "!", not_found: "?", unavailable: "⌁", locked: "🔒︎" };

export function StatePanel({ kind, title, children, actions, role }: {
  kind: StateKind; title?: ReactNode; children?: ReactNode; actions?: ReactNode; role?: "alert" | "status";
}) {
  return (
    <div className={`panel state state-${kind}`} role={role ?? (kind === "error" ? "alert" : undefined)}>
      <span className="state-icon" aria-hidden="true">{ICON[kind]}</span>
      <div className="stack state-body">
        {title && <h2 className="state-title">{title}</h2>}
        {children && <div className="muted">{children}</div>}
        {actions && <div className="row">{actions}</div>}
      </div>
    </div>
  );
}

type T = (key: string, params?: Record<string, string | number>) => string;

/** Server pages: the backend answered with an error or did not answer. 404 is handled by notFound(). */
export function ServerError({ t, locale, status }: { t: T; locale: string; status?: number }) {
  const offline = !status || status >= 500;
  return (
    <div className="section">
      <StatePanel kind="error" title={t(offline ? "state.unavailable_title" : "state.error_title")}
        actions={<><a className="btn" href="">{t("state.reload")}</a><a className="btn ghost" href={`/${locale}`}>{t("state.home")}</a></>}>
        {t(offline ? "state.unavailable_text" : "state.error_text")}{status ? ` (${status})` : ""}
      </StatePanel>
    </div>
  );
}
