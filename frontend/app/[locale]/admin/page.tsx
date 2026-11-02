"use client";

import { Breadcrumbs, PageHead } from "@/components/nav";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, Price, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api, uid } from "@/lib/api";
import { date } from "@/lib/i18n";

const TABS = ["users", "profiles", "finance", "refunds", "orders", "audit", "ops"] as const;

function Users() {
  const { t } = useApp();
  const [q, setQ] = useState("");
  const { data, reload } = useLoad<any>(`/admin/users${q ? `?q=${encodeURIComponent(q)}` : ""}`, [q]);
  const sellers = useLoad<any>("/admin/sellers");
  const [role, setRole] = useState<Record<string, string>>({});
  const [seller, setSeller] = useState("");
  const act = useAction();
  return (
    <div className="stack">
      <input placeholder={t("admin.search_users")} value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
      <label className="field" style={{ maxWidth: 320 }}>{t("admin.seller_for_role")}<select value={seller} onChange={(e) => setSeller(e.target.value)}><option value="">—</option>{sellers.data?.items.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <div className="scroll-x"><table><thead><tr><th>E-mail</th><th>{t("admin.roles")}</th><th>{t("admin.grant")}</th></tr></thead>
        <tbody>{data?.items.map((u: any) => (
          <tr key={u.id}><td>{u.email ?? <span className="muted">{t("admin.anonymized")}</span>}<div className="hint">{u.display_name}</div></td>
            <td>{u.roles.map((r: string) => <span key={r} className="badge gray" style={{ marginRight: 4 }}>{r} <button className="btn ghost small" style={{ minHeight: 18, padding: "0 4px" }} aria-label={t("admin.revoke")} onClick={async () => { if (await act.run(() => api(`/admin/users/${u.id}/roles/${r}`, { method: "DELETE" }))) reload(); }}>×</button></span>)}</td>
            <td><div className="row"><select value={role[u.id] ?? "developer"} aria-label={t("admin.role_for", { email: u.email })} onChange={(e) => setRole({ ...role, [u.id]: e.target.value })} style={{ width: 150 }}>
              {["user", "developer", "seller", "fulfillment", "moderator", "content_editor", "admin"].map((r) => <option key={r} value={r}>{t(`roles.${r}`)}</option>)}</select>
              <button className="btn small" onClick={async () => { if (await act.run(() => api(`/admin/users/${u.id}/roles`, { body: { role: role[u.id] ?? "developer", seller_id: seller || null } }))) reload(); }}>{t("admin.grant")}</button></div></td></tr>))}</tbody></table></div>
      <ErrorBox error={act.error} />
    </div>
  );
}

function Profiles() {
  const { t } = useApp();
  const { data, reload } = useLoad<any>("/admin/profiles");
  const [f, setF] = useState({ slug: "alpha-demo-v1", name_ru: "", name_en: "", carrier_center_hz: 255, beat_hz: 10, source_gain_db: -6, tone_gain_dbfs: -30, fade_in_seconds: 0.5, fade_out_seconds: 0.5 });
  const [curve, setCurve] = useState("");  // NAP-021: "t_seconds = beat_hz" per line
  const act = useAction();
  return (
    <div className="stack">
      <p className="hint">{t("admin.profiles_hint")}</p>
      <table><thead><tr><th>slug</th><th>v</th><th>{t("account.status")}</th><th>params</th><th></th></tr></thead>
        <tbody>{data?.items.map((p: any) => <tr key={p.id}><td>{p.slug}</td><td>{p.version}</td><td><StatusBadge ns="profile_status" value={p.status} /></td>
          <td className="hint">{Object.entries(p.params).map(([k, v]) => k === "beat_curve" ? `${t("admin.curve_short")}: ${(v as number[][]).map(([ts, b]) => `${ts} s → ${b} Hz`).join(", ")}` : `${k}=${v}`).join(", ")}</td>
          <td>{p.status === "active" && <button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/admin/profiles/${p.id}/retire`, { method: "POST" }))) reload(); }}>{t("admin.retire")}</button>}</td></tr>)}</tbody></table>
      <form className="panel stack" onSubmit={async (e) => {
        e.preventDefault();
        const { slug, name_ru, name_en, ...params } = f;
        const beat_curve = curve.split("\n").map((l) => l.split("=").map((x) => Number(x.trim().replace(",", ".")))).filter((p) => p.length === 2 && p.every((x) => Number.isFinite(x)));
        if (await act.run(() => api("/admin/profiles", { body: { slug, names: { ru: name_ru || slug, en: name_en || slug }, params: beat_curve.length ? { ...params, beat_curve } : params } }))) reload();
      }}>
        <h3>{t("admin.new_profile_version")}</h3>
        <div className="form wide">{Object.entries(f).map(([k, v]) => (
          <label key={k} className="field">{k}<input value={v} type={typeof v === "number" ? "number" : "text"} step="any" onChange={(e) => setF({ ...f, [k]: typeof v === "number" ? Number(e.target.value) : e.target.value })} /></label>))}</div>
        <label className="field">{t("admin.curve")}<textarea rows={4} value={curve} onChange={(e) => setCurve(e.target.value)} placeholder={"0 = 10\n900 = 4"} /><span className="hint">{t("admin.curve_hint")}</span></label>
        <div><button className="btn">{t("admin.create_version")}</button></div>
      </form>
      <ErrorBox error={act.error} />
    </div>
  );
}

function Finance() {
  const { t } = useApp();
  const { data, reload } = useLoad<any>("/admin/finance");
  const [order, setOrder] = useState(""); const [amount, setAmount] = useState(0);
  const act = useAction();
  if (!data) return <Loading />;
  return (
    <div className="stack">
      <p className="hint">{t("admin.provider", { p: data.provider })}</p>
      {data.developers.map((d: any) => (
        <div key={d.id} className="panel flat stack"><strong>{d.name}</strong>
          {d.summary.map((s: any) => <div key={s.currency} className="row">{s.currency}: {t("fin.available")} <Price minor={s.available_minor} currency={s.currency} />, {t("fin.paid")} <Price minor={s.paid_minor} currency={s.currency} />
            {s.negative && <span className="badge bad">{t("fin.negative")}</span>}
            <button className="btn small" disabled={s.available_minor <= 0} onClick={async () => { if (await act.run(() => api("/admin/payouts", { body: { developer_id: d.id, currency: s.currency } }))) reload(); }}>{t("admin.payout")}</button></div>)}
        </div>))}
      <form className="panel row" onSubmit={async (e) => { e.preventDefault(); if (await act.run(() => api("/admin/refunds/digital", { body: { order_id: order, amount_minor: Math.round(amount * 100), reason: "admin" }, headers: { "Idempotency-Key": uid() } }))) reload(); }}>
        <input placeholder={t("admin.order_id")} value={order} onChange={(e) => setOrder(e.target.value)} style={{ maxWidth: 340 }} />
        <input type="number" step="0.01" value={amount} onChange={(e) => setAmount(Number(e.target.value))} style={{ maxWidth: 120 }} aria-label={t("account.amount")} />
        <button className="btn ghost">{t("admin.refund_digital")}</button>
      </form>
      <details><summary>{t("admin.ledger")}</summary><table><tbody>{data.ledger_balances.map((b: any, i: number) => <tr key={i}><td className="hint">{b.account}</td><td>{b.currency}</td><td>{b.balance_minor}</td></tr>)}</tbody></table></details>
      <ErrorBox error={act.error} />
    </div>
  );
}

function RefundRequest({ r, onDone }: { r: any; onDone: () => void }) {  // NAP-024
  const { t, locale } = useApp();
  const [amount, setAmount] = useState((r.amount_minor / 100).toFixed(2));
  const [note, setNote] = useState("");
  const act = useAction();
  const decide = (approve: boolean) => act.run(() => api(`/admin/refund-requests/${r.id}/decision`, {
    body: { approve, note, amount_minor: approve ? Math.round(Number(amount) * 100) : undefined } })).then((x) => { if (x) onDone(); });
  return <article className="panel flat stack" data-testid="refund-request">
    <div className="row"><strong>{r.order.app_slug}</strong><StatusBadge ns="refund_status" value={r.status} /></div>
    <div className="hint">{t("admin.refund_buyer")}: {r.buyer.email} · {t("admin.refund_paid")}: <Price minor={r.order.amount_minor} currency={r.currency} />
      {r.order.refunded_minor > 0 && <> · {t("account.refunded")}: <Price minor={r.order.refunded_minor} currency={r.currency} /></>} · {date(r.created_at, locale, true)}</div>
    <div><span className="hint">{t("admin.refund_reason")}:</span> {r.withdrawal ? <strong>{t("refund.withdrawal")}</strong> : null} {r.reason === "withdrawal" ? "" : r.reason}</div>
    {r.note && <div><span className="hint">{t("refund.answer")}:</span> {r.note}</div>}
    {r.status === "requested" && <div className="stack">
      <div className="form wide">
        <label className="field">{t("admin.refund_amount")} ({r.currency})
          <input type="number" step="0.01" min="0.01" max={(r.amount_minor / 100).toFixed(2)} value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
        <label className="field">{t("admin.refund_note")}
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} /></label>
      </div>
      <p className="hint">{t("admin.refund_note_hint")}</p>
      <ErrorBox error={act.error} />
      <div className="row"><button type="button" className="btn small" disabled={act.pending} onClick={() => decide(true)}>{t("admin.refund_approve")}</button>
        <button type="button" className="btn ghost small" disabled={act.pending || note.trim().length < 3} onClick={() => decide(false)}>{t("admin.refund_reject")}</button></div>
    </div>}
  </article>;
}

function Refunds() {
  const { t } = useApp();
  const [status, setStatus] = useState("requested");
  const { data, reload } = useLoad<any>(`/admin/refund-requests?status=${status}`);
  return <div className="stack">
    <label className="field">{t("admin.refund_filter")}
      <select value={status} onChange={(e) => setStatus(e.target.value)}>
        {["requested", "pending", "succeeded", "failed", "rejected"].map((s) => <option key={s} value={s}>{t(`refund_status.${s}`)}</option>)}
        <option value="all">{t("admin.refund_all")}</option>
      </select></label>
    {!data ? <Loading /> : !data.items.length ? <Empty>{t("admin.refunds_empty")}</Empty>
      : data.items.map((r: any) => <RefundRequest key={r.id} r={r} onDone={reload} />)}
  </div>;
}

function Orders() {
  const { t, locale } = useApp();
  const { data } = useLoad<any>("/admin/orders");
  if (!data) return <Loading />;
  return <table><tbody>{data.items.map((o: any) => <tr key={o.id}><td>{o.number}</td><td>{o.seller.name}</td><td><StatusBadge ns="order_status" value={o.status} /> <StatusBadge ns="payment_status" value={o.payment_status} /> <StatusBadge ns="fulfillment_status" value={o.fulfillment_status} /></td><td>{date(o.created_at, locale)}</td></tr>)}</tbody></table>;
}

function Audit() {
  const { locale } = useApp();
  const { data } = useLoad<any>("/admin/audit?limit=200");
  if (!data) return <Loading />;
  return <div className="scroll-x"><table><tbody>{data.items.map((a: any, i: number) => <tr key={i}><td className="hint">{date(a.at, locale, true)}</td><td>{a.action}</td><td className="hint">{a.entity_type}:{a.entity_id}</td><td className="hint">{JSON.stringify(a.data).slice(0, 120)}</td><td className="hint">{a.request_id?.slice(0, 8)}</td></tr>)}</tbody></table></div>;
}

function Ops() {
  const { t } = useApp();
  const { data, reload } = useLoad<any>("/admin/ops");
  const i18n = useLoad<any>("/admin/i18n/missing");
  const act = useAction();
  const { locale } = useApp();
  if (!data) return <Loading />;
  const b = data.backup;
  return (
    <div className="stack">
      {b ? <div className={`notice ${b.stale ? "bad" : "ok"}`}>{b.status === "ok" ? t("admin.backup_ok", { date: date(b.finished_at, locale, true), file: b.file }) : t("admin.backup_failed", { date: date(b.finished_at, locale, true), msg: b.message })}{b.stale && b.status === "ok" ? ` ${t("admin.backup_stale")}` : ""}</div>
        : <div className="notice warn">{t("admin.backup_none")}</div>}
      {data.emails_failed > 0 && <div className="notice warn">{t("admin.emails_failed", { count: data.emails_failed })}</div>}
      <dl className="kv"><dt>{t("admin.unprocessed")}</dt><dd>{data.webhooks_unprocessed}</dd><dt>{t("admin.outbox")}</dt><dd>{data.outbox_pending}</dd>
        {Object.entries(data.jobs).map(([k, v]) => <Fragment key={k}><dt>{t(`job_state.${k}`)}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
      <button className="btn ghost" onClick={async () => { const r = await act.run(() => api("/admin/ops/run", { method: "POST" })); if (r) reload(); }}>{t("admin.run_ops")}</button>
      {data.failed_webhooks.length > 0 && <table><tbody>{data.failed_webhooks.map((w: any) => <tr key={w.event_id}><td>{w.source}</td><td>{w.type}</td><td>{w.attempts}</td><td className="hint">{w.error}</td></tr>)}</tbody></table>}
      {i18n.data && <p className="hint">{t("admin.i18n_missing", { ru: i18n.data.ru.length, en: i18n.data.en.length })}</p>}
      <Link href="/api/v1/docs" target="_blank">OpenAPI</Link>
      <ErrorBox error={act.error} />
    </div>
  );
}

function Admin() {
  const { t, locale } = useApp();
  const router = useRouter();
  const tab = (useSearchParams()?.get("tab") as (typeof TABS)[number]) || "users";
  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { label: t("admin.title") }]} />
      <PageHead title={t("admin.title")} lead={t("admin.lead")} />
      <div className="tabs" role="tablist">{TABS.map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/${locale}/admin?tab=${x}`)}>{t(`admin.tab_${x}`)}</button>)}</div>
      {tab === "users" && <Users />}{tab === "profiles" && <Profiles />}{tab === "finance" && <Finance />}{tab === "refunds" && <Refunds />}
      {tab === "orders" && <Orders />}{tab === "audit" && <Audit />}{tab === "ops" && <Ops />}
    </div>
  );
}

export default function AdminPage() {
  return <div className="section"><RequireAuth role="admin"><Suspense><Admin /></Suspense></RequireAuth></div>;
}
