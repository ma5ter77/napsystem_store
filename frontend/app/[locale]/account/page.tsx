"use client";

import { Breadcrumbs, PageHead } from "@/components/nav";
import { StatusTimeline } from "@/components/StatusTimeline";
import { refundSteps } from "@/lib/steps";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, Price, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { deleteSavedAddress, SavedAddressList, useSavedAddresses } from "@/components/SavedAddresses";
import { date } from "@/lib/i18n";
import { LegalText } from "@/components/LegalText";

const TABS = ["purchases", "subscriptions", "devices", "history", "privacy", "notifications", "addresses"] as const;

function RefundCell({ o, onDone }: { o: any; onDone: () => void }) {  // NAP-024
  const { t } = useApp();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [withdrawal, setWithdrawal] = useState(false);
  const [sent, setSent] = useState(false);
  const act = useAction();
  const rq = o.refund_request;
  return <div className="stack">
    {rq && <div><StatusTimeline value={refundSteps(rq)} ns="refund" label={t("steps.refund_progress")} />{rq.note && <div className="hint">{t("refund.answer")}: {rq.note}</div>}</div>}
    {sent && <p className="hint" role="status">{t("refund.sent")}</p>}
    {o.can_request_refund && !open && <div><button type="button" className="btn ghost small" onClick={() => { setOpen(true); setSent(false); }}>{t("refund.request")}</button></div>}
    {open && <form className="stack" onSubmit={async (e) => {
      e.preventDefault();
      if (await act.run(() => api(`/purchases/${o.id}/refund-request`, { body: { reason, withdrawal } }))) { setOpen(false); setReason(""); setWithdrawal(false); setSent(true); onDone(); }
    }}>
      <label className="check"><input type="checkbox" checked={withdrawal} onChange={(e) => setWithdrawal(e.target.checked)} /> {t("refund.withdrawal")}</label>
      <label className="field">{withdrawal ? t("refund.reason_optional") : t("refund.reason")}
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} minLength={withdrawal ? 0 : 10} maxLength={1000} required={!withdrawal} rows={3} /></label>
      <p className="hint">{withdrawal ? t("refund.withdrawal_hint") : t("refund.reason_hint")}</p>
      <ErrorBox error={act.error} />
      <div className="row"><button className="btn small" disabled={act.pending || (!withdrawal && reason.trim().length < 10)}>{t("refund.send")}</button>
        <button type="button" className="btn ghost small" onClick={() => setOpen(false)}>{t("refund.cancel")}</button></div>
    </form>}
  </div>;
}

function Purchases() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/purchases");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data.items.length) return <Empty action={<a className="btn" href={`/${locale}/apps`}>{t("home.cta_apps")}</a>}>{t("account.no_purchases")}</Empty>;
  return <div className="scroll-x"><table><thead><tr><th>{t("account.app")}</th><th>{t("account.kind")}</th><th>{t("account.amount")}</th><th>{t("account.status")}</th><th>{t("account.date")}</th><th>{t("admin.tab_refunds")}</th></tr></thead>
    <tbody>{data.items.map((o: any) => <tr key={o.id} data-testid={`purchase-${o.id}`}><td>{o.app_slug}</td><td>{t(`kinds.${o.kind}`)}</td><td><Price minor={o.amount_minor} currency={o.currency} />{o.refunded_minor > 0 && <div className="hint">{t("account.refunded")}: <Price minor={o.refunded_minor} currency={o.currency} /></div>}</td><td><StatusBadge ns="digital_status" value={o.status} /></td><td>{date(o.created_at, locale)}</td><td><RefundCell o={o} onDone={reload} /></td></tr>)}</tbody></table></div>;
}

function Subs() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/subscriptions");
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data.items.length) return <Empty action={<a className="btn ghost" href={`/${locale}/apps?price=subscription`}>{t("home.cta_apps")}</a>}>{t("account.no_subs")}</Empty>;
  const real = data.items.some((s: any) => s.provider === "stripe");
  return (
    <div className="stack">
      {real && (  // NAP-012: cards, invoices and renewals are managed in the provider's secure portal
        <div className="panel flat spread">
          <span>{t("billing.portal_hint")}</span>
          <button className="btn ghost small" disabled={act.pending} onClick={async () => {
            const r = await act.run(() => api("/billing/portal", { body: { return_path: "/account?tab=subscriptions" } }));
            if (r?.url) window.location.assign(r.url);
          }}>{t("billing.portal")}</button>
        </div>
      )}
      {data.items.map((s: any) => (
        <div key={s.id} className="panel flat stack">
          <div className="spread"><strong>{s.app_slug}</strong><StatusBadge ns="sub_status" value={s.status} /></div>
          <div><Price minor={s.price.amount_minor} currency={s.price.currency} /> / {t(`interval.${s.price.interval}`)} · {t("app.paid_until", { date: date(s.paid_until, locale) })}</div>
          <div className="hint">{s.periods.map((p: any) => `${date(p.start, locale)} – ${date(p.end, locale)} (${t(`period.${p.status}`)})`).join("; ")}</div>
          <div className="row">
            {s.cancel_at_period_end ? <span className="hint">{t("account.autorenew_off")}</span> :
              <button className="btn ghost small" disabled={act.pending} onClick={async () => { if (await act.run(() => api(`/subscriptions/${s.id}/cancel`, { method: "POST" }))) reload(); }}>{t("account.cancel_autorenew")}</button>}
            {!s.cancel_at_period_end && s.provider === "mock" && <>
              <button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/test/subscriptions/${s.id}/renew`, { body: { outcome: "success" } }))) reload(); }}>{t("account.sim_renew")}</button>
              <button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/test/subscriptions/${s.id}/renew`, { body: { outcome: "fail" } }))) reload(); }}>{t("account.sim_fail")}</button>
            </>}
          </div>
        </div>
      ))}
      <ErrorBox error={act.error} />
    </div>
  );
}

function Devices() {
  const { t } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/devices");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data.items.length) return <Empty>{t("account.no_devices")}</Empty>;
  return <div className="stack">{data.items.map((d: any) => (
    <div key={d.id} className="panel flat"><strong>{d.name}</strong> {d.simulated && <span className="badge warn">{t("player.simulator")}</span>}
      <div className="hint">{t("account.caps")}: {(d.capabilities.provides ?? []).map((c: string) => t(`caps.${c}`)).join(", ") || "—"} · API {d.capabilities.api_version} · {t(`separation.${d.capabilities.channel_separation}`)}</div></div>))}</div>;
}

function History() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/me/sessions");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data.items.length) return <Empty>{t("account.no_history")}</Empty>;
  return <div className="stack">{data.items.map((s: any) => (
    <div key={s.id} className="panel flat stack">
      <div className="spread"><strong>{s.app.slug} — {s.program_session.title}</strong><StatusBadge ns="session_state" value={s.state} /></div>
      <div className="hint">{date(s.created_at, locale, true)} · {t("account.q_pre")}: {t(`qstatus.${s.pre_status}`)} · {t("account.q_post")}: {t(`qstatus.${s.post_status}`)}</div>
      {Object.keys(s.delta).length > 0 && <div>{Object.entries(s.delta).map(([k, v]: any) => `${t(`metrics.${k}`)} ${s.pre[k]} → ${s.post[k]} (${v > 0 ? "+" : ""}${v})`).join("; ")}</div>}
      {s.measurements.length > 0 && <div className="hint">{s.measurements.map((m: any) => `${t(`mtypes.${m.type}`)}: ${m.value} ${m.unit}${m.synthetic ? ` (${t("account.synthetic")})` : ""}`).join("; ")}</div>}
    </div>))}<p className="hint">{t("player.self_report_note")}</p></div>;
}

function Privacy() {
  const { t, locale } = useApp();
  const { data, reload } = useLoad<any>("/me/consent");
  const act = useAction();
  return (
    <div className="panel stack">
      <h2>{t("account.consent_title")}</h2>
      <p>{t("account.consent_text")}</p>
      <p className="hint"><LegalText text={t("legal.measurements_note")} locale={locale} labels={{ measurements: t("legal.measurements") }} /></p>
      <label className="check"><input type="checkbox" checked={!!data?.store_measurements} onChange={async (e) => {
        if (await act.run(() => api("/me/consent", { method: "PUT", body: { granted: e.target.checked } }))) reload();
      }} /> {t("account.consent_box")}</label>
      <button className="btn ghost" onClick={async () => { const r = await act.run(() => api("/me/measurements", { method: "DELETE" })); if (r) alertDeleted(r.deleted); }}>{t("account.delete_measurements")}</button>
      <ErrorBox error={act.error} />
    </div>
  );
  function alertDeleted(n: number) { act.setError({ message: t("account.deleted", { count: n }) }); }
}

function Notifications() {
  const { t, locale } = useApp();
  const { data, loading } = useLoad<any>("/me/notifications");
  if (loading && !data) return <Loading />;
  if (!data?.items.length) return <Empty>{t("account.no_notifications")}</Empty>;
  return <div className="stack">{data.items.map((n: any) => <div key={n.id} className="panel flat" lang={n.locale}><strong>{n.subject}</strong><div>{n.body}</div><div className="hint">{date(n.created_at, locale, true)}</div></div>)}</div>;
}

function Addresses() {  // NAP-023
  const { t } = useApp();
  const { data, error, loading, reload } = useSavedAddresses();
  const act = useAction();
  const [done, setDone] = useState(false);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data?.items.length) return <Empty>{t("account.no_addresses")}</Empty>;
  return <div className="stack">
    <SavedAddressList items={data.items} action={t("address.delete")} pending={act.pending}
      onAction={async (id) => { setDone(false); if (await act.run(() => deleteSavedAddress(id).then(() => true))) { setDone(true); reload(); } }} />
    {done && <p className="hint" role="status">{t("address.deleted")}</p>}
    <ErrorBox error={act.error} />
    <p className="hint">{t("address.recheck_hint")}</p>
  </div>;
}

function Account() {
  const { t, locale } = useApp();
  const router = useRouter();
  const tab = (useSearchParams()?.get("tab") as (typeof TABS)[number]) || "purchases";
  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { label: t("account.title") }]} />
      <PageHead title={t("account.title")} lead={t("account.lead")} />
      <div className="tabs" role="tablist">
        {TABS.map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/${locale}/account?tab=${x}`)}>{t(`account.tab_${x}`)}</button>)}
      </div>
      {tab === "purchases" && <Purchases />}
      {tab === "subscriptions" && <Subs />}
      {tab === "devices" && <Devices />}
      {tab === "history" && <History />}
      {tab === "privacy" && <Privacy />}
      {tab === "notifications" && <Notifications />}
      {tab === "addresses" && <Addresses />}
    </div>
  );
}

export default function AccountPage() {
  return <div className="section"><RequireAuth><Suspense><Account /></Suspense></RequireAuth></div>;
}
