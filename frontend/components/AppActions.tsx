"use client";

import Link from "next/link";
import { LegalText } from "./LegalText";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, uid } from "@/lib/api";
import { date } from "@/lib/i18n";
import { useApp } from "./Providers";
import { ErrorBox, useAction } from "./ui";

export function AppActions({ app }: { app: any }) {
  const { t, locale, me } = useApp();
  const router = useRouter();
  const [access, setAccess] = useState<any>(null);
  const [preview, setPreview] = useState<string | null>(null);
  // NAP-064: free days this buyer gets with the subscription (a guest sees the author's offer)
  const offered = app.prices?.find((p: any) => p.kind === "subscription")?.trial_days ?? 0;
  const [trial, setTrial] = useState<number>(offered);
  const act = useAction();
  useEffect(() => {
    if (me) api(`/entitlements/${app.id}`).then(setAccess).catch(() => setAccess(null));
    if (me && offered) api<any>(`/checkout/trial/${app.id}`).then((r) => setTrial(r.trial_days)).catch(() => {});
  }, [me, app.id, offered]);

  async function buy(kind: string) {
    if (me === null) { router.push(`/${locale}/login?next=/${locale}/apps/${app.slug}`); return; }
    if (me === undefined) return;
    const r = await act.run(() => api("/checkout/digital", { body: { app_id: app.id, kind }, headers: { "Idempotency-Key": uid() } }));
    if (!r) return;
    if (r.checkout_url) router.push(`/${locale}${r.checkout_url}`);
    else router.push(`/${locale}/library`);
  }

  async function loadPreview() {
    const r = await act.run(() => api(`/catalog/apps/${app.slug}/preview`));
    if (r) setPreview(r.url);
  }

  const forSale = app.sales_status === "active" && app.prices?.length;
  return (
    <div className="stack">
      {access?.has_access ? (
        <div className="row">
          <Link className="btn" href={`/${locale}/play/${app.slug}`}>{t("app.open_player")}</Link>
          {access.paid_until && <span className="muted">{t("app.paid_until", { date: date(access.paid_until, locale) })}</span>}
        </div>
      ) : forSale ? (
        <div className="row">
          {app.prices.map((p: any) => (
            <button key={p.id} className="btn" disabled={act.pending || me === undefined} onClick={() => buy(p.kind)}>
              {p.kind === "free" ? t("app.get_free") : p.kind === "subscription" ? (trial ? t("app.start_trial", { count: trial }) : t("app.subscribe")) : t("app.buy")}
            </button>
          ))}
        </div>
      ) : <p className="muted">{t("app.not_for_sale")}</p>}
      {!access?.has_access && forSale && offered > 0 && (
        <p className="hint" data-testid="trial-note">{trial ? t("app.trial_note", { count: trial }) : t("app.trial_used")}</p>)}
      {!access?.has_access && forSale && app.prices.some((p: any) => p.kind !== "free") && (
        <p className="hint"><LegalText text={t("legal.buy_note")} locale={locale} labels={{ terms: t("legal.terms") }} /> {t("vat.note")}</p>)}
      {app.has_public_preview && !preview && <button className="btn ghost" onClick={loadPreview}>{t("app.listen_preview")}</button>}
      {preview && (
        <div>
          <audio controls src={preview} preload="none" style={{ width: "100%" }} />
          <p className="hint">{t("app.preview_note")}</p>
        </div>
      )}
      <ErrorBox error={act.error} />
    </div>
  );
}
