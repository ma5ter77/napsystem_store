"use client";

// NAP-022: supplier product list + "new product" form on the supplier cabinet
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

export function SellerProducts() {
  const { t, locale } = useApp();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad<any>("/seller/products");
  const cats = useLoad<any>("/categories?kind=hardware");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ category: "", brand: "", model: "" });
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const create = async () => {
    const p = await act.run(() => api<any>("/seller/products", { body: { ...f, category: f.category || cats.data?.items?.[0]?.slug } }));
    if (p) router.push(`/${locale}/seller/products/${p.id}?tab=skus`);
  };
  return (
    <div className="stack">
      <div className="spread"><span /><button className="btn" onClick={() => setOpen(!open)} aria-expanded={open}>{t("sp.new")}</button></div>
      {open && (
        <div className="panel stack">
          <h3>{t("sp.new")}</h3>
          <p className="hint">{t("sp.new_hint")}</p>
          <div className="grid3">
            <label className="field">{t("fields.category")}
              <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
                {(cats.data?.items ?? []).map((c: any) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
              </select></label>
            <label className="field">{t("fields.brand")}<input value={f.brand} maxLength={80} onChange={(e) => setF({ ...f, brand: e.target.value })} /></label>
            <label className="field">{t("fields.model")}<input value={f.model} maxLength={80} onChange={(e) => setF({ ...f, model: e.target.value })} /></label>
          </div>
          <div><button className="btn" disabled={act.pending || !f.brand.trim() || !f.model.trim()} onClick={create}>{t("sp.create")}</button></div>
          <ErrorBox error={act.error} />
        </div>
      )}
      {!data.items.length ? <Empty>{t("sp.empty")}</Empty> : (
        <div className="scroll-x"><table>
          <thead><tr><th>{t("sp.col_product")}</th><th>{t("account.status")}</th><th>{t("sp.col_skus")}</th><th>{t("sp.col_lang")}</th><th>{t("sp.col_last")}</th></tr></thead>
          <tbody>{data.items.map((p: any) => (
            <tr key={p.id}>
              <td><Link href={`/${locale}/seller/products/${p.id}`}>{p.title}</Link><div className="hint">{p.slug}{p.is_demo ? ` · ${t("sp.demo")}` : ""}</div></td>
              <td><StatusBadge ns="product_status" value={p.status} /></td>
              <td>{p.skus}</td>
              <td>{p.languages.map((l: string) => l.toUpperCase()).join(", ") || "—"}</td>
              <td>{p.last ? <>{t(`sp.hist.${p.last.action}`)} <span className="hint">{date(p.last.at, locale)}</span>{p.last.comment && <div className="hint">«{p.last.comment}»</div>}</> : "—"}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}
