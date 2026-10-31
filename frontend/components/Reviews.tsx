"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { date, langName, num1 } from "@/lib/i18n";
import { useApp } from "./Providers";
import { Empty, ErrorBox, Loading, useAction, useLoad } from "./ui";

const REASONS = ["spam", "abuse", "personal_data", "advertising", "medical_claims", "other"] as const;

/** Read-only stars; the number is always in the accessible name. */
export function Stars({ value, label }: { value: number; label?: string }) {
  const full = Math.round(value);
  return (
    <span className="stars" role="img" aria-label={label ?? `${value}/5`}>
      {[1, 2, 3, 4, 5].map((i) => <span key={i} aria-hidden="true" className={i <= full ? "on" : ""}>★</span>)}
    </span>
  );
}

function StarInput({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  const { t } = useApp();
  return (
    <fieldset className="star-input">
      <legend>{t("rev.your_rating")}</legend>
      {[1, 2, 3, 4, 5].map((i) => (
        <label key={i} title={t("rev.stars_n", { count: i })}>
          <input type="radio" name="rating" value={i} checked={value === i} onChange={() => onChange(i)} required />
          <span aria-hidden="true" className={i <= value ? "on" : ""}>★</span>
          <span className="sr-only">{t("rev.stars_n", { count: i })}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function RatingSummary({ s }: { s: any }) {
  const { t, locale } = useApp();
  const max = Math.max(1, ...Object.values<number>(s.distribution));
  return (
    <div className="rating-summary">
      <div className="rating-big">
        {s.average !== null ? <><strong>{num1(s.average, locale)}</strong><Stars value={s.average} label={t("rev.avg_label", { avg: num1(s.average, locale) })} /></>
          : <strong className="muted">—</strong>}
        <span className="hint">{s.count ? t("rev.ratings_n", { count: s.count }) : t("rev.no_ratings")}</span>
        {!s.enough && s.count > 0 && <span className="hint">{t("rev.few", { n: s.min_for_average })}</span>}
        {s.current_version.average !== null && <span className="hint">{t("rev.current_version", { avg: num1(s.current_version.average, locale), count: s.current_version.count })}</span>}
      </div>
      <ul className="rating-bars" aria-label={t("rev.distribution")}>
        {[5, 4, 3, 2, 1].map((k) => {
          const n = s.distribution[String(k)] ?? 0;
          return <li key={k}><span>{k}★</span><span className="bar"><i style={{ width: `${(n / max) * 100}%` }} /></span><span className="hint">{n}</span></li>;
        })}
      </ul>
    </div>
  );
}

function MyReview({ base, mine, onSaved }: { base: string; mine: any; onSaved: () => void }) {
  const { t, locale } = useApp();
  const [editing, setEditing] = useState(!mine);
  const [rating, setRating] = useState<number>(mine?.rating ?? 0);
  const [title, setTitle] = useState<string>(mine?.title ?? "");
  const [body, setBody] = useState<string>(mine?.body ?? "");
  const act = useAction();
  if (!editing && mine) {
    return (
      <div className="panel flat stack">
        <div className="spread"><strong>{t("rev.your_review")}</strong>
          <span className="row"><button className="btn ghost small" onClick={() => setEditing(true)}>{t("rev.edit")}</button>
            <button className="btn ghost small" disabled={act.pending} onClick={async () => {
              if (await act.run(() => api(`${base}/review`, { method: "DELETE" })) !== undefined) onSaved();
            }}>{t("rev.delete")}</button></span></div>
        <Stars value={mine.rating} label={t("rev.stars_n", { count: mine.rating })} />
        {mine.title && <strong>{mine.title}</strong>}
        {mine.body && <p className="pre">{mine.body}</p>}
        {mine.status === "pending" && <div className="notice warn">{t("rev.pending")}</div>}
        {mine.status === "hidden" && <div className="notice bad">{t("rev.hidden")}</div>}
        <ErrorBox error={act.error} />
      </div>
    );
  }
  return (
    <form className="panel flat stack" onSubmit={async (e) => {
      e.preventDefault();
      if (!rating) return;
      const r = await act.run(() => api(`${base}/review`, { method: "PUT", body: { rating, title: title || null, body: body || null, lang: locale } }));
      if (r) { setEditing(false); onSaved(); }
    }}>
      <strong>{mine ? t("rev.edit_title") : t("rev.write_title")}</strong>
      <StarInput value={rating} onChange={setRating} />
      <label className="field">{t("rev.title")}<input maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label className="field">{t("rev.body")}<textarea rows={4} maxLength={4000} value={body} onChange={(e) => setBody(e.target.value)} />
        <span className="hint">{t("rev.body_hint", { n: 4000 - body.length })}</span></label>
      {title && !body && <span className="err-text">{t("rev.title_needs_body")}</span>}
      <ErrorBox error={act.error} />
      <div className="row"><button className="btn" disabled={act.pending || !rating || (!!title && !body)}>{t("rev.publish")}</button>
        {mine && <button type="button" className="btn ghost" onClick={() => setEditing(false)}>{t("rev.cancel")}</button>}</div>
      <p className="hint">{t("rev.rules")}</p>
    </form>
  );
}

function ReviewItem({ r, onChange }: { r: any; onChange: () => void }) {
  const { t, locale, me } = useApp();
  const [report, setReport] = useState<"" | "review" | "reply">("");
  const [reason, setReason] = useState<string>("spam");
  const [sent, setSent] = useState(false);
  const [helpful, setHelpful] = useState({ n: r.helpful_count, mine: !!r.helpful_by_me });
  const act = useAction();
  return (
    <article className="review" lang={r.lang}>
      <div className="spread"><span className="row"><Stars value={r.rating} label={t("rev.stars_n", { count: r.rating })} />{r.title && <strong>{r.title}</strong>}</span>
        <span className="hint">{r.author ?? t("rev.deleted_user")} · {date(r.created_at, locale)}{r.edited ? ` · ${t("rev.edited")}` : ""}{r.edited_by_moderator ? ` · ${t("rev.edited_by_moderator")}` : ""}</span></div>
      <div className="hint">{r.verified_purchase ? <><span className="badge ok">{t("rev.verified")}</span> {t("rev.variant", { v: r.version })}</> : t("rev.version", { v: r.version })}{r.lang !== locale ? ` · ${langName(r.lang, locale)}` : ""}</div>
      {r.body && <p className="pre">{r.body}</p>}
      {r.reply && <div className="reply"><strong>{t("rev.dev_reply")}</strong> <span className="hint">{date(r.reply.updated_at, locale)}{r.reply.edited_by_moderator ? ` · ${t("rev.edited_by_moderator")}` : ""}</span><p className="pre">{r.reply.body}</p></div>}
      {me && !r.mine && (
        <div className="row">
          <button className="btn ghost small" aria-pressed={helpful.mine} disabled={act.pending} onClick={async () => {
            const x = await act.run(() => api(`/reviews/${r.id}/helpful`, { method: "POST" }));
            if (x) setHelpful({ n: x.helpful_count, mine: x.helpful_by_me });
          }}>{t("rev.helpful")} · {helpful.n}</button>
          {!sent && <button className="btn ghost small" onClick={() => setReport(report ? "" : "review")}>{t("rev.report")}</button>}
          {sent && <span className="hint">{t("rev.report_sent")}</span>}
        </div>
      )}
      {!me && helpful.n > 0 && <div className="hint">{t("rev.helpful_n", { count: helpful.n })}</div>}
      {report && (
        <form className="row" onSubmit={async (e) => {
          e.preventDefault();
          if (await act.run(() => api(`/reviews/${r.id}/report`, { body: { target: report, reason } }))) { setSent(true); setReport(""); }
        }}>
          {r.reply && <label className="field">{t("rev.report_what")}<select value={report} onChange={(e) => setReport(e.target.value as any)}>
            <option value="review">{t("rev.target_review")}</option><option value="reply">{t("rev.target_reply")}</option></select></label>}
          <label className="field">{t("rev.reason")}<select value={reason} onChange={(e) => setReason(e.target.value)}>
            {REASONS.map((x) => <option key={x} value={x}>{t(`rev.reasons.${x}`)}</option>)}</select></label>
          <div><button className="btn small">{t("rev.send_report")}</button></div>
        </form>
      )}
      <ErrorBox error={act.error} />
    </article>
  );
}

/** App page section: summary, own review form and the public list. */
export function ReviewsSection({ slug, kind = "app", path }: { slug: string; kind?: "app" | "product"; path?: string }) {
  const base = kind === "app" ? `/catalog/apps/${slug}` : `/hardware/products/${slug}`;
  const page = path ?? (kind === "app" ? `apps/${slug}` : `devices/${slug}`);
  const { t, locale } = useApp();
  const [sort, setSort] = useState("helpful");
  const [version, setVersion] = useState("");
  const [lang, setLang] = useState("");
  const [limit, setLimit] = useState(10);
  const qs = new URLSearchParams({ sort, limit: String(limit) });
  if (version) qs.set("version", version);
  if (lang) qs.set("lang", lang);
  const { data, error, loading, reload } = useLoad<any>(`${base}/reviews?${qs}`);
  return (
    <section className="panel stack" aria-labelledby="reviews-h">
      <h2 id="reviews-h">{t("rev.title_section")}</h2>
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} onRetry={reload} /> : (
        <>
          <RatingSummary s={data.summary} />
          {data.eligibility.can_review || data.mine
            ? <MyReview key={data.mine?.updated_at ?? "new"} base={base} mine={data.mine} onSaved={reload} />
            : <p className="hint">{data.eligibility.reason === "login_required"
              ? <Link href={`/${locale}/login?next=/${locale}/${page}`}>{t("rev.why.login_required")}</Link>
              : t(`rev.why.${data.eligibility.reason}`)}</p>}
          {data.summary.count > 0 && (
            <div className="filters">
              <label className="field">{t("rev.sort")}<select value={sort} onChange={(e) => setSort(e.target.value)}>
                {["helpful", "new", "high", "low"].map((x) => <option key={x} value={x}>{t(`rev.sorts.${x}`)}</option>)}</select></label>
              {data.versions.length > 1 && <label className="field">{kind === "app" ? t("rev.filter_version") : t("rev.filter_variant")}<select value={version} onChange={(e) => setVersion(e.target.value)}>
                <option value="">{t("catalog.any")}</option>{data.versions.map((v: string) => <option key={v} value={v}>{v}</option>)}</select></label>}
              <label className="field">{t("rev.filter_lang")}<select value={lang} onChange={(e) => setLang(e.target.value)}>
                <option value="">{t("catalog.any")}</option><option value="ru">{langName("ru", locale)}</option><option value="en">{langName("en", locale)}</option></select></label>
            </div>
          )}
          {data.items.length === 0 ? (data.summary.count > 0 && <p className="muted">{t("rev.none_filtered")}</p>)
            : <div className="stack">{data.items.map((r: any) => <ReviewItem key={r.id} r={r} onChange={reload} />)}</div>}
          {data.total > data.items.length && <div><button className="btn ghost" onClick={() => setLimit(limit + 10)}>{t("rev.more")}</button></div>}
        </>
      )}
    </section>
  );
}

/** Developer cabinet tab: feed with filters, reply form, monthly trend. */
export function DeveloperReviews({ scope = "developer" }: { scope?: "developer" | "seller" }) {
  const { t, locale } = useApp();
  const idParam = scope === "developer" ? "app_id" : "product_id";
  const [appId, setAppId] = useState("");
  const [filter, setFilter] = useState("");
  const [rating, setRating] = useState("");
  const qs = new URLSearchParams({ limit: "50" });
  if (appId) qs.set(idParam, appId);
  if (filter) qs.set("filter", filter);
  if (rating) qs.set("rating", rating);
  const { data, error, loading, reload } = useLoad<any>(`/${scope}/reviews?${qs}`);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <div className="filters">
        <label className="field">{scope === "developer" ? t("rev.app") : t("rev.product")}<select value={appId} onChange={(e) => setAppId(e.target.value)}>
          <option value="">{scope === "developer" ? t("rev.all_apps") : t("rev.all_products")}</option>{data.apps.map((a: any) => <option key={a.id} value={a.id}>{a.slug}</option>)}</select></label>
        <label className="field">{t("rev.show")}<select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">{t("rev.all_reviews")}</option><option value="unanswered">{t("rev.unanswered")}</option></select></label>
        <label className="field">{t("rev.stars")}<select value={rating} onChange={(e) => setRating(e.target.value)}>
          <option value="">{t("catalog.any")}</option>{[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{n}★</option>)}</select></label>
      </div>
      {data.summary && <RatingSummary s={data.summary} />}
      {data.trend.length > 0 && (
        <table><caption className="hint">{t("rev.trend")}</caption><thead><tr><th>{t("rev.month")}</th><th>{t("rev.avg")}</th><th>{t("rev.count")}</th></tr></thead>
          <tbody>{data.trend.map((x: any) => <tr key={x.month}><td>{x.month}</td><td>{num1(x.average, locale)}</td><td>{x.count}</td></tr>)}</tbody></table>
      )}
      <p className="hint">{scope === "developer" ? t("rev.dev_rules") : t("rev.seller_rules")}</p>
      {!data.items.length ? <Empty>{t("rev.dev_empty")}</Empty> : data.items.map((r: any) => <DevReviewItem key={r.id + (r.reply?.updated_at ?? "")} r={r} onChange={reload} scope={scope} />)}
    </div>
  );
}

function DevReviewItem({ r, onChange, scope }: { r: any; onChange: () => void; scope: "developer" | "seller" }) {
  const { t, locale } = useApp();
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(r.reply?.body ?? "");
  const act = useAction();
  return (
    <article className="review panel flat" lang={r.lang}>
      <div className="spread"><span className="row"><Stars value={r.rating} label={t("rev.stars_n", { count: r.rating })} /><strong>{r.title}</strong></span>
        <span className="hint">{r.app.slug} · {scope === "developer" ? t("rev.version", { v: r.version }) : t("rev.variant", { v: r.version })} · {date(r.created_at, locale)}</span></div>
      {r.status === "pending" && <span className="badge warn">{t("rev.status.pending")}</span>}
      {r.body && <p className="pre">{r.body}</p>}
      {r.reply && !editing && (
        <div className="reply"><strong>{t("rev.your_reply")}</strong> {r.reply.status !== "published" && <span className="badge warn">{t(`rev.status.${r.reply.status}`)}</span>}
          <p className="pre">{r.reply.body}</p>
          <div className="row"><button className="btn ghost small" onClick={() => setEditing(true)}>{t("rev.edit")}</button>
            <button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/${scope}/reviews/${r.id}/reply`, { method: "DELETE" })) !== undefined) onChange(); }}>{t("rev.delete")}</button></div></div>
      )}
      {(!r.reply || editing) && (
        <form className="stack" onSubmit={async (e) => {
          e.preventDefault();
          if (await act.run(() => api(`/${scope}/reviews/${r.id}/reply`, { method: "PUT", body: { body } }))) { setEditing(false); onChange(); }
        }}>
          <label className="field">{t("rev.reply_label")}<textarea rows={3} required maxLength={4000} value={body} onChange={(e) => setBody(e.target.value)} /></label>
          <div className="row"><button className="btn small" disabled={act.pending || !body.trim()}>{t("rev.send_reply")}</button>
            {editing && <button type="button" className="btn ghost small" onClick={() => setEditing(false)}>{t("rev.cancel")}</button>}</div>
        </form>
      )}
      <ErrorBox error={act.error} />
    </article>
  );
}

/** Moderation: pending reviews/replies and reported items. */
export function ModerationReviews() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/moderation/reviews");
  const act = useAction();
  const decide = (id: string, target: string, action: string) =>
    act.run(() => api(`/moderation/reviews/${id}/decision`, { body: { target, action } })).then((x) => x && reload());
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <h2>{t("rev.mod_title")}</h2>
      {!data.items.length ? <Empty>{t("rev.mod_empty")}</Empty> : data.items.map((r: any) => (
        <article key={r.id} className="review panel flat" lang={r.lang}>
          <div className="spread"><span className="row"><Stars value={r.rating} label={t("rev.stars_n", { count: r.rating })} /><strong>{r.title}</strong></span>
            <span className="hint">{r.app.slug} · {r.author} · {date(r.updated_at, locale, true)}</span></div>
          {r.body && <p className="pre">{r.body}</p>}
          <div className="row"><span className={`badge ${r.status === "published" ? "ok" : r.status === "hidden" ? "gray" : "warn"}`}>{t(`rev.status.${r.status}`)}</span>
            {r.moderation_note && <span className="hint">{t("rev.flags")}: {r.moderation_note}</span>}</div>
          {r.reports.length > 0 && <ul className="hint">{r.reports.map((x: any, i: number) => <li key={i}>{t(`rev.target_${x.target}`)}: {t(`rev.reasons.${x.reason}`)}{x.comment ? ` — ${x.comment}` : ""}</li>)}</ul>}
          <div className="row">
            <button className="btn small" disabled={act.pending} onClick={() => decide(r.id, "review", "publish")}>{t("rev.mod_publish")}</button>
            <button className="btn ghost small" disabled={act.pending} onClick={() => decide(r.id, "review", "hide")}>{t("rev.mod_hide")}</button>
          </div>
          {r.reply && (
            <div className="reply"><strong>{t("rev.dev_reply")}</strong> <span className={`badge ${r.reply.status === "published" ? "ok" : "warn"}`}>{t(`rev.status.${r.reply.status}`)}</span>
              <p className="pre">{r.reply.body}</p>
              <div className="row">
                <button className="btn small" disabled={act.pending} onClick={() => decide(r.id, "reply", "publish")}>{t("rev.mod_publish_reply")}</button>
                <button className="btn ghost small" disabled={act.pending} onClick={() => decide(r.id, "reply", "hide")}>{t("rev.mod_hide_reply")}</button>
              </div></div>
          )}
        </article>
      ))}
      <ErrorBox error={act.error} />
    </div>
  );
}
