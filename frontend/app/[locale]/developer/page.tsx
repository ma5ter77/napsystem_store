"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AppCover } from "@/components/Art";
import { useApp } from "@/components/Providers";
import { DeveloperReviews } from "@/components/Reviews";
import { Empty, ErrorBox, errorFields, Loading, Price, RequireAuth, StatusBadge, useAction, useDraft, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";
import { normalizeSlug, SLUG_PATTERN } from "@/lib/slug";

const TABS = ["apps", "reviews", "finance", "analytics", "keys"] as const;

function NewApp({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useApp();
  const cats = useLoad<any>("/categories?kind=app");
  const [f, setF, clear] = useDraft("new-app", { slug: "", author_name: "", category: "meditation", title_ru: "", title_en: "", content_language: "none" });
  const act = useAction();
  const bad = new Set(errorFields(act.error));
  return (
    <form className="panel stack" onSubmit={async (e) => {
      e.preventDefault();
      const metadata: any = {};
      if (f.title_ru) metadata.ru = { title: f.title_ru };
      if (f.title_en) metadata.en = { title: f.title_en };
      const r = await act.run(() => api("/developer/apps", { body: { slug: f.slug, author_name: f.author_name, category: f.category, metadata, content_language: f.content_language } }));
      if (r) { clear(); onCreated(r.id); }
    }}>
      <h2>{t("dev.new_app")}</h2>
      <div className="form wide">
        <label className="field">{t("dev.slug")}<input required pattern={SLUG_PATTERN} minLength={3} maxLength={80} title={t("dev.slug_hint")}
          autoCapitalize="none" autoCorrect="off" spellCheck={false} aria-invalid={bad.has("slug") || undefined} aria-describedby="slug-hint"
          value={f.slug} onChange={(e) => setF({ ...f, slug: normalizeSlug(e.target.value) })} /><span className="hint" id="slug-hint">{t("dev.slug_hint")}</span></label>
        <label className="field">{t("dev.author")}<input required maxLength={120} aria-invalid={bad.has("author_name") || undefined} value={f.author_name} onChange={(e) => setF({ ...f, author_name: e.target.value })} /></label>
        <label className="field">{t("app.category")}<select aria-invalid={bad.has("category") || undefined} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{cats.data?.items.map((c: any) => <option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label>
        <label className="field">{t("dev.title_ru")}<input value={f.title_ru} onChange={(e) => setF({ ...f, title_ru: e.target.value })} /></label>
        <label className="field">{t("dev.title_en")}<input value={f.title_en} onChange={(e) => setF({ ...f, title_en: e.target.value })} /></label>
        <label className="field">{t("app.audio_lang")}<select value={f.content_language} onChange={(e) => setF({ ...f, content_language: e.target.value })}>
          <option value="none">{t("dev.lang_none")}</option><option value="ru">Русский</option><option value="en">English</option></select></label>
      </div>
      <p className="hint">{t("dev.one_title_required")}</p>
      <ErrorBox error={act.error} />
      <div><button className="btn" disabled={act.pending}>{t("dev.create")}</button></div>
    </form>
  );
}

function Apps() {
  const { t, locale } = useApp();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad<any>("/developer/apps");
  const [showNew, setShowNew] = useState(false);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <div className="spread"><h2>{t("dev.my_apps")}</h2><button className="btn" onClick={() => setShowNew(!showNew)}>{t("dev.new_app")}</button></div>
      {showNew && <NewApp onCreated={(id) => router.push(`/${locale}/developer/apps/${id}`)} />}
      {!data.items.length ? <Empty>{t("dev.no_apps")}</Empty> : (
        <div className="grid">
          {data.items.map((a: any) => (
            <Link key={a.id} className="card" href={`/${locale}/developer/apps/${a.id}`}>
              <div className="art"><AppCover a={a} /></div>
              <h3>{a.title}</h3>
              <div className="meta">{a.slug} · v{a.latest_version?.label} <StatusBadge ns="version_state" value={a.latest_version?.state} /></div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function Finance() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/developer/finance");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <p className="hint">{t("dev.split_note", { pct: data.commission_bps / 100, days: data.hold_days })}</p>
      {!data.summary.length ? <Empty>{t("dev.no_sales")}</Empty> : data.summary.map((s: any) => (
        <div key={s.currency} className="panel stack">
          <h3>{s.currency}</h3>
          <dl className="kv">
            <dt>{t("fin.sales")}</dt><dd>{s.sales}</dd>
            <dt>{t("fin.sub_periods")}</dt><dd>{s.subscription_periods} ({t("fin.active_subs", { count: data.active_subscriptions })})</dd>
            <dt>{t("fin.gross")}</dt><dd><Price minor={s.gross_minor} currency={s.currency} /></dd>
            <dt>{t("fin.refunds")}</dt><dd><Price minor={s.refunds_minor} currency={s.currency} /></dd>
            <dt>{t("fin.commission")}</dt><dd><Price minor={s.commission_minor} currency={s.currency} /></dd>
            <dt>{t("fin.accrued")}</dt><dd><Price minor={s.accrued_minor} currency={s.currency} /></dd>
            <dt>{t("fin.pending")}</dt><dd><Price minor={s.pending_minor} currency={s.currency} /></dd>
            <dt>{t("fin.available")}</dt><dd><Price minor={s.available_minor} currency={s.currency} /> {s.negative && <span className="badge bad">{t("fin.negative")}</span>}</dd>
            <dt>{t("fin.in_transit")}</dt><dd><Price minor={s.in_transit_minor} currency={s.currency} /></dd>
            <dt>{t("fin.paid")}</dt><dd><Price minor={s.paid_minor} currency={s.currency} /></dd>
          </dl>
        </div>
      ))}
      {data.orders.length > 0 && <div className="scroll-x"><table><thead><tr><th>{t("account.date")}</th><th>{t("account.kind")}</th><th>{t("account.amount")}</th><th>{t("account.status")}</th></tr></thead>
        <tbody>{data.orders.map((o: any) => <tr key={o.id}><td>{date(o.created_at, locale)}</td><td>{t(`kinds.${o.kind}`)}</td><td><Price minor={o.amount_minor} currency={o.currency} /></td><td><StatusBadge ns="digital_status" value={o.status} /></td></tr>)}</tbody></table></div>}
      {data.payouts.length > 0 && <div className="stack"><h3>{t("fin.payouts")}</h3>{data.payouts.map((p: any) => <div key={p.id}><Price minor={p.amount_minor} currency={p.currency} /> <StatusBadge ns="payout_status" value={p.status} /> {p.mock && <span className="badge warn">{t("common.test")}</span>} {date(p.created_at, locale)}</div>)}</div>}
    </div>
  );
}

function Analytics() {
  const { t } = useApp();
  const { data, error, loading } = useLoad<any>("/developer/analytics");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return <div className="stack">{data.items.map((a: any) => (
    <div key={a.app_id} className="panel flat"><strong>{a.slug}</strong> · {t("an.orders", { count: a.paid_orders })}
      <div className="hint">{a.usage.suppressed ? t("an.suppressed", { n: a.usage.min_group }) :
        t("an.usage", { users: a.usage.users, sessions: a.usage.sessions, rate: Math.round((a.usage.completion_rate ?? 0) * 100) })}</div></div>))}
    <p className="hint">{t("an.privacy")}</p></div>;
}

function Keys() {
  const { t, locale } = useApp();
  const { data, reload } = useLoad<any>("/developer/keys");
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["apps:read", "apps:write", "assets:write", "conversions:write", "conversions:read"]);
  const [created, setCreated] = useState<string | null>(null);
  const act = useAction();
  if (!data) return <Loading />;
  return (
    <div className="stack">
      <div className="notice">{t("keys.server_only")}</div>
      <form className="panel stack" onSubmit={async (e) => { e.preventDefault(); const r = await act.run(() => api("/developer/keys", { body: { name, scopes } })); if (r) { setCreated(r.key); setName(""); reload(); } }}>
        <label className="field">{t("keys.name")}<input required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <fieldset style={{ border: 0, padding: 0 }}><legend>{t("keys.scopes")}</legend><div className="row">
          {data.available_scopes.map((s: string) => <label key={s} className="check"><input type="checkbox" checked={scopes.includes(s)} onChange={() => setScopes(scopes.includes(s) ? scopes.filter((x) => x !== s) : [...scopes, s])} /> {s}</label>)}
        </div></fieldset>
        <ErrorBox error={act.error} />
        <div><button className="btn">{t("keys.create")}</button></div>
      </form>
      {created && <div className="notice warn"><strong>{t("keys.once")}</strong><div><code style={{ wordBreak: "break-all" }}>{created}</code></div></div>}
      <table><thead><tr><th>{t("keys.name")}</th><th>{t("keys.prefix")}</th><th>{t("keys.scopes")}</th><th>{t("keys.last_used")}</th><th></th></tr></thead>
        <tbody>{data.items.map((k: any) => <tr key={k.id}><td>{k.name}</td><td><code>{k.prefix}</code></td><td className="hint">{k.scopes.join(", ")}</td><td>{date(k.last_used_at, locale, true)}</td>
          <td>{k.revoked ? <span className="badge gray">{t("keys.revoked")}</span> : <button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/developer/keys/${k.id}`, { method: "DELETE" }))) reload(); }}>{t("keys.revoke")}</button>}</td></tr>)}</tbody></table>
      <p className="hint">{t("keys.sdk_hint")}</p>
    </div>
  );
}

function Developer() {
  const { t, locale, me, refreshMe } = useApp();
  const router = useRouter();
  const tab = (useSearchParams()?.get("tab") as (typeof TABS)[number]) || "apps";
  const act = useAction();
  if (me && !me.roles.includes("developer")) {
    return (
      <div className="panel stack" style={{ maxWidth: 640 }}>
        <h1>{t("dev.become_title")}</h1><p>{t("dev.become_text")}</p>
        <div><button className="btn" onClick={async () => { if (await act.run(() => api("/me/roles/developer", { method: "POST" }))) refreshMe(); }}>{t("dev.become")}</button></div>
        <ErrorBox error={act.error} />
      </div>
    );
  }
  return (
    <RequireAuth role="developer">
      <div className="stack">
        <h1>{t("dev.title")}</h1>
        <div className="tabs" role="tablist">{TABS.map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/${locale}/developer?tab=${x}`)}>{t(`dev.tab_${x}`)}</button>)}</div>
        {tab === "apps" && <Apps />}
        {tab === "reviews" && <DeveloperReviews />}
        {tab === "finance" && <Finance />}
        {tab === "analytics" && <Analytics />}
        {tab === "keys" && <Keys />}
      </div>
    </RequireAuth>
  );
}

export default function DeveloperPage() {
  return <div className="section"><RequireAuth><Suspense><Developer /></Suspense></RequireAuth></div>;
}
