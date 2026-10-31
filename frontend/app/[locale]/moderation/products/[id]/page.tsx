"use client";

// NAP-022: moderator reviews a supplier product card and approves it into the catalog or returns it with a comment.
import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, Price, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

function ProductReview() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useApp();
  const { data: p, error, loading, reload } = useLoad<any>(`/moderation/products/${id}`);
  const [comment, setComment] = useState("");
  const [done, setDone] = useState<{ decision: string; next: string | null } | null>(null);
  const act = useAction();
  if (loading && !p) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const decide = async (decision: string) => {
    if (await act.run(() => api(`/moderation/products/${id}/decision`, { body: { decision, comment } }))) {
      setComment(""); reload();
      const q = await api<any>("/moderation/products").catch(() => ({ items: [] }));
      setDone({ decision, next: q.items.find((x: any) => x.id !== id)?.id ?? null });
    }
  };
  return (
    <div className="stack">
      <Link href={`/${locale}/moderation`}>{t("mod.title")}</Link>
      <div className="spread"><div><h1>{p.brand} {p.model}</h1><div className="hint">{t("mod.supplier")}: {p.seller} · {p.category_name} · {p.slug}</div></div><StatusBadge ns="product_status" value={p.status} /></div>
      <div className="panel flat stack">
        <ul className="checklist">{p.checks.map((c: any) => (
          <li key={c.key} className={c.ok ? "ok" : c.optional ? "warn" : "bad"}><span aria-hidden="true">{c.ok ? "✓" : c.optional ? "!" : "✗"}</span><span>{t(`sp.check.${c.key}`)}</span></li>))}</ul>
      </div>
      <div className="two">
        {["ru", "en"].map((l) => p.texts[l] ? (
          <div key={l} className="panel stack" lang={l}>
            <strong>{l.toUpperCase()}</strong><h3>{p.texts[l].title}</h3>
            {p.texts[l].summary && <p>{p.texts[l].summary}</p>}
            <p className="hint" style={{ whiteSpace: "pre-wrap" }}>{p.texts[l].description}</p>
            {(p.texts[l].in_box ?? []).length > 0 && <><strong>{t("sp.in_box")}</strong><ul>{p.texts[l].in_box.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></>}
            {p.texts[l].return_terms && <p className="hint">{t("sp.return_terms")}: {p.texts[l].return_terms}</p>}
            {p.texts[l].delivery_terms && <p className="hint">{t("sp.delivery_terms")}: {p.texts[l].delivery_terms}</p>}
          </div>) : <div key={l} className="panel flat hint">{l.toUpperCase()}: —</div>)}
      </div>
      <div className="panel stack">
        <div className="kv">
          <span>{t("sp.warranty")}</span><span>{p.warranty_months}</span>
          <span>{t("sp.freight")}</span><span>{t(`sp.freight_${p.freight_mode}`)}</span>
          <span>{t("sp.destinations")}</span><span>{p.destinations.join(", ") || "—"}</span>
          {p.made_to_order && <><span>{t("sp.lead_time")}</span><span>{p.lead_time_days}</span></>}
          {Object.entries(p.specs ?? {}).map(([k, v]) => <Fragment key={k}><span>{k}</span><span>{String(v)}</span></Fragment>)}
        </div>
      </div>
      <div className="scroll-x"><table>
        <thead><tr><th>SKU</th><th>{t("sp.variant")}</th><th>{t("sp.price")}</th><th>{t("sp.weight")}</th><th>{t("sp.dims")}</th><th>{t("sp.origin")}</th><th>{t("sp.hs")}</th><th>{t("sp.battery")}</th></tr></thead>
        <tbody>{p.skus.map((s: any) => (
          <tr key={s.id}><td>{s.code}{!s.active && <div className="hint">{t("product_status.draft")}</div>}</td>
            <td>{Object.entries(s.variant ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "—"}</td>
            <td><Price minor={s.price_minor} currency={s.currency} /></td><td>{s.weight_g}</td>
            <td>{s.length_mm}×{s.width_mm}×{s.height_mm}{s.package_count > 1 ? ` (${s.package_count})` : ""}</td>
            <td>{s.origin_country || "—"}</td><td>{s.hs_code ?? "—"}{s.customs_description && <div className="hint">{s.customs_description}</div>}</td>
            <td>{t(`battery.${s.contains_battery}`)}</td></tr>))}</tbody>
      </table></div>
      {p.history.length > 0 && <div className="panel flat stack">{p.history.map((h: any, i: number) => <div key={i}>{t(`sp.hist.${h.action}`)} <span className="hint">{date(h.at, locale, true)}</span>{h.comment && <> — «{h.comment}»</>}</div>)}</div>}
      {done && (
        <div className={`notice ${done.decision === "reject" ? "warn" : "ok"}`} role="status">
          <strong>{t(`mod.product_done_${done.decision}`)}</strong> {t("mod.product_supplier_notified")}
          <div className="row" style={{ marginTop: 8 }}>
            {done.next ? <Link className="btn small" href={`/${locale}/moderation/products/${done.next}`}>{t("mod.next")}</Link> : <span className="hint">{t("mod.queue_empty_now")}</span>}
            <Link className="btn ghost small" href={`/${locale}/moderation`}>{t("mod.back_to_queue")}</Link>
          </div>
        </div>
      )}
      {p.status === "submitted" && (
        <div className="panel stack">
          <label className="field">{t("mod.comment")}<textarea value={comment} onChange={(e) => setComment(e.target.value)} /></label>
          <div className="row">
            <button className="btn" disabled={act.pending} onClick={() => decide("approve")}>{t("mod.approve")}</button>
            <button className="btn danger" disabled={act.pending || !comment.trim()} onClick={() => decide("reject")}>{t("mod.reject")}</button>
          </div>
          {!comment.trim() && <p className="hint">{t("mod.reject_needs_comment")}</p>}
          <ErrorBox error={act.error} />
        </div>
      )}
    </div>
  );
}

export default function ProductModerationPage() {
  return <div className="section"><RequireAuth role={["moderator", "admin"]}><ProductReview /></RequireAuth></div>;
}
