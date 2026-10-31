"use client";

// NAP-084: moderator catalog management — every app, product and review, with search, filters and actions.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/Providers";
import { Stars } from "@/components/Reviews";
import { Empty, ErrorBox, Loading, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

function Filters({ q, setQ, status, setStatus, options, ns }: { q: string; setQ: (x: string) => void; status: string; setStatus: (x: string) => void; options: string[]; ns: string }) {
  const { t } = useApp();
  return (
    <div className="row">
      <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("mm.search")} aria-label={t("mm.search")} style={{ minWidth: 220 }} />
      <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("account.status")}>
        <option value="">{t("mm.status_all")}</option>
        {options.map((o) => <option key={o} value={o}>{t(`${ns}.${o}`)}</option>)}
      </select>
    </div>
  );
}

export function ManageApps() {
  const { t, locale } = useApp();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad<any>(`/moderation/catalog/apps?q=${encodeURIComponent(q)}&status=${status}`, [q, status]);
  return (
    <div className="stack">
      <Filters q={q} setQ={setQ} status={status} setStatus={setStatus} options={["published", "withdrawn", "unpublished", "deleted"]} ns="mm.app_filter" />
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} onRetry={reload} /> : !data.items.length ? <Empty>{t("mm.nothing_found")}</Empty> : (
        <div className="scroll-x"><table>
          <thead><tr><th>{t("mm.col_app")}</th><th>{t("mm.col_author")}</th><th>{t("account.status")}</th><th>{t("mm.col_version")}</th></tr></thead>
          <tbody>{data.items.map((a: any) => (
            <tr key={a.id}>
              <td><Link href={`/${locale}/moderation/catalog/apps/${a.id}`}>{a.title}</Link><div className="hint">{a.slug}{a.is_demo ? ` · ${t("sp.demo")}` : ""}</div></td>
              <td>{a.author}</td>
              <td className="status-line">
                {a.deleted_at ? <span className="badge bad">{t("mm.deleted")}</span>
                  : <>{a.published ? <span className="badge ok">{t("mm.in_store")}</span> : <span className="badge gray">{t("mm.not_published")}</span>}
                    {a.sales_status === "withdrawn" && <span className="badge warn">{t("mm.sales_stopped")}</span>}</>}
              </td>
              <td>{a.latest_label ? <>v{a.latest_label} <StatusBadge ns="version_state" value={a.latest_state} /></> : "—"}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}

export function ManageProducts() {
  const { t, locale } = useApp();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ seller_id: "", category: "", brand: "", model: "" });
  const cats = useLoad<any>("/categories?kind=hardware");
  const act = useAction();
  const { data, error, loading, reload } = useLoad<any>(`/moderation/catalog/products?q=${encodeURIComponent(q)}&status=${status}`, [q, status]);
  const create = async () => {
    const p = await act.run(() => api<any>("/moderation/catalog/products", { body: { ...f, seller_id: f.seller_id || data?.sellers?.[0]?.id, category: f.category || cats.data?.items?.[0]?.slug } }));
    if (p) router.push(`/${locale}/moderation/catalog/products/${p.id}?tab=skus`);
  };
  return (
    <div className="stack">
      <div className="spread">
        <Filters q={q} setQ={setQ} status={status} setStatus={setStatus} options={["draft", "submitted", "published", "rejected", "deleted"]} ns="product_status" />
        <button className="btn" onClick={() => setOpen(!open)} aria-expanded={open}>{t("sp.new")}</button>
      </div>
      {open && (
        <div className="panel stack">
          <div className="grid3">
            <label className="field">{t("mod.supplier")}<select value={f.seller_id} onChange={(e) => setF({ ...f, seller_id: e.target.value })}>
              {(data?.sellers ?? []).map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
            <label className="field">{t("fields.category")}<select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
              {(cats.data?.items ?? []).map((c: any) => <option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label>
            <span />
            <label className="field">{t("fields.brand")}<input value={f.brand} onChange={(e) => setF({ ...f, brand: e.target.value })} /></label>
            <label className="field">{t("fields.model")}<input value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} /></label>
          </div>
          <div><button className="btn" disabled={act.pending || !f.brand.trim() || !f.model.trim()} onClick={create}>{t("sp.create")}</button></div>
          <ErrorBox error={act.error} />
        </div>
      )}
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} onRetry={reload} /> : !data.items.length ? <Empty>{t("mm.nothing_found")}</Empty> : (
        <div className="scroll-x"><table>
          <thead><tr><th>{t("sp.col_product")}</th><th>{t("mod.supplier")}</th><th>{t("account.status")}</th><th>{t("sp.col_skus")}</th><th>{t("sp.col_lang")}</th></tr></thead>
          <tbody>{data.items.map((p: any) => (
            <tr key={p.id}>
              <td><Link href={`/${locale}/moderation/catalog/products/${p.id}`}>{p.title}</Link><div className="hint">{p.slug}</div></td>
              <td>{p.seller}</td>
              <td><StatusBadge ns="product_status" value={p.status} />{p.deleted_reason && <div className="hint">«{p.deleted_reason}»</div>}</td>
              <td>{p.skus}</td>
              <td>{p.languages.map((l: string) => l.toUpperCase()).join(", ") || "—"}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}

function ReviewRow({ r, reload }: { r: any; reload: () => void }) {
  const { t, locale } = useApp();
  const act = useAction();
  const [mode, setMode] = useState<null | "edit" | "reply">(null);
  const [reason, setReason] = useState("");
  const [title, setTitle] = useState(r.title ?? "");
  const [body, setBody] = useState(r.body ?? "");
  const [replyBody, setReplyBody] = useState(r.reply?.body ?? "");
  const run = async (action: string, extra: any = {}, target = "review") => {
    if (await act.run(() => api(`/moderation/reviews/${r.id}/decision`, { body: { target, action, note: reason, ...extra } }))) { setMode(null); setReason(""); reload(); }
  };
  const target = r.app?.kind === "product" ? `/${locale}/devices/${r.app.slug}` : `/${locale}/apps/${r.app?.slug}`;
  return (
    <div className="panel flat stack">
      <div className="spread">
        <div><Stars value={r.rating} /> <strong>{r.title}</strong> <span className="hint">— {r.author ?? "—"}, {date(r.created_at, locale)}</span></div>
        <div className="row"><StatusBadge ns="rev_status" value={r.status} />{r.edited_by_moderator && <span className="badge gray">{t("rev.edited_by_moderator")}</span>}{r.open_reports > 0 && <span className="badge warn">{t("mm.reports", { count: r.open_reports })}</span>}</div>
      </div>
      <div className="hint"><Link href={target}>{r.app?.title ?? r.app?.slug}</Link></div>
      {mode === "edit" ? (
        <div className="stack">
          <label className="field">{t("fields.title")}<input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} /></label>
          <label className="field">{t("fields.description")}<textarea rows={4} value={body} maxLength={4000} onChange={(e) => setBody(e.target.value)} /></label>
        </div>
      ) : <p style={{ whiteSpace: "pre-wrap" }}>{r.body}</p>}
      {r.reply && (mode === "reply"
        ? <label className="field">{t("mm.reply_text")}<textarea rows={3} value={replyBody} onChange={(e) => setReplyBody(e.target.value)} /></label>
        : <div className="notice"><strong>{t("mm.reply")}:</strong> {r.reply.body} {r.reply.status && r.reply.status !== "published" && <StatusBadge ns="rev_status" value={r.reply.status} />}</div>)}
      <div className="row">
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("mm.reason")} aria-label={t("mm.reason")} maxLength={300} style={{ minWidth: 220 }} />
        {mode === "edit" ? <><button className="btn small" disabled={act.pending || !reason.trim()} onClick={() => run("edit", { title, body })}>{t("common.save")}</button><button className="btn ghost small" onClick={() => setMode(null)}>{t("mm.cancel")}</button></>
          : mode === "reply" ? <><button className="btn small" disabled={act.pending || !reason.trim() || !replyBody.trim()} onClick={() => run("edit", { body: replyBody }, "reply")}>{t("common.save")}</button><button className="btn ghost small" onClick={() => setMode(null)}>{t("mm.cancel")}</button></>
          : <>
            <button className="btn ghost small" onClick={() => setMode("edit")}>{t("mm.edit")}</button>
            {r.reply && <button className="btn ghost small" onClick={() => setMode("reply")}>{t("mm.edit_reply")}</button>}
            {r.status !== "published" && <button className="btn ghost small" disabled={act.pending} onClick={() => run("publish")}>{t("mm.show")}</button>}
            {r.status === "published" && <button className="btn ghost small" disabled={act.pending} onClick={() => run("hide")}>{t("mm.hide")}</button>}
            {r.status !== "deleted" && <button className="btn danger small" disabled={act.pending || !reason.trim()} onClick={() => run("delete")}>{t("mm.delete")}</button>}
          </>}
      </div>
      <ErrorBox error={act.error} />
    </div>
  );
}

export function ManageReviews() {
  const { t } = useApp();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useLoad<any>(`/moderation/reviews/all?q=${encodeURIComponent(q)}&status=${status}&kind=${kind}&limit=20&offset=${offset}`, [q, status, kind, offset]);
  return (
    <div className="stack">
      <div className="row">
        <Filters q={q} setQ={(x) => { setQ(x); setOffset(0); }} status={status} setStatus={(x) => { setStatus(x); setOffset(0); }} options={["published", "pending", "hidden", "deleted"]} ns="rev_status" />
        <select value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0); }} aria-label={t("mm.kind")}>
          <option value="">{t("mm.kind_all")}</option><option value="app">{t("mm.kind_app")}</option><option value="product">{t("mm.kind_product")}</option>
        </select>
      </div>
      <p className="hint">{t("mm.reviews_hint")}</p>
      {loading && !data ? <Loading /> : error ? <ErrorBox error={error} onRetry={reload} /> : !data.items.length ? <Empty>{t("mm.nothing_found")}</Empty> : (
        <>
          {data.items.map((r: any) => <ReviewRow key={r.id + r.status + r.updated_at} r={r} reload={reload} />)}
          <div className="row">
            <button className="btn ghost small" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>←</button>
            <span className="hint">{offset + 1}–{Math.min(offset + 20, data.total)} / {data.total}</span>
            <button className="btn ghost small" disabled={offset + 20 >= data.total} onClick={() => setOffset(offset + 20)}>→</button>
          </div>
        </>
      )}
    </div>
  );
}
