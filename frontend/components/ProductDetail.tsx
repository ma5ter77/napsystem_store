import { Fragment } from "react";
import { notFound } from "next/navigation";
import { ProductArt } from "@/components/Art";
import { ProductGallery } from "@/components/ProductGallery";
import { availabilityText, CompatBadge } from "@/components/cards";
import { AddToCart } from "@/components/AddToCart";
import { BomBuilder } from "@/components/BomBuilder";
import { langName, Locale, money, num1, translator } from "@/lib/i18n";
import { ReviewsSection, Stars } from "@/components/Reviews";
import { serverApi, serverTranslator } from "@/lib/server";
import { ServerError } from "@/components/State";

export async function ProductDetail({ locale, slug, app }: { locale: Locale; slug: string; app?: string }) {
  const t = (await serverTranslator(locale));
  const r = await serverApi<any>(`/hardware/products/${slug}`, locale);
  if (!r.ok) {
    if (r.status === 404) notFound();
    return <ServerError t={t} locale={locale} status={r.status} />;
  }
  const p = r.data;
  return (
    <div className="section two">
      <div className="stack">
        {p.photos?.length ? <ProductGallery photos={p.photos} title={p.title} />
          : <div className="hero-art" style={{ aspectRatio: "4/3" }}><ProductArt category={p.category.slug} hue={p.hue} title={p.title} /></div>}
        {p.is_demo && <div className="notice test">{t("hw.demo_data")}</div>}
      </div>
      <div className="stack">
        <h1 lang={p.lang ?? undefined}>{p.title}</h1>
        {p.rating?.average != null && <a href="#reviews-h" className="row hint"><Stars value={p.rating.average} label={t("rev.card", { avg: num1(p.rating.average, locale), count: p.rating.count })} /> {num1(p.rating.average, locale)} · {t("rev.ratings_n", { count: p.rating.count })}</a>}
        {p.fallback && p.lang && <span className="badge gray">{t("common.original_lang", { lang: langName(p.lang, locale) })}</span>}
        <p lang={p.lang ?? undefined}>{p.description}</p>
        {p.skus.map((s: any) => (
          <div key={s.id} className="panel flat stack">
            <div className="spread">
              <div><strong>{money(s.price_minor, s.currency, locale)}</strong> <span className="muted">SKU {s.code}</span></div>
              <span>{availabilityText(s.availability, t)}</span>
            </div>
            <div className="row">{s.compatibility.map((c: any, i: number) => (
              <span key={i} title={[c.source, c.tested_config].filter(Boolean).join(" — ")}>{t(`feature.${c.feature}`)}: <CompatBadge status={c.status} t={t} /></span>
            ))}</div>
            <AddToCart skuId={s.id} disabled={s.availability.type === "out_of_stock"} />
          </div>
        ))}
        <p className="hint">{t("vat.note")}</p>
        <dl className="kv">
          <dt>{t("hw.seller")}</dt><dd>{p.seller.name}{p.seller.kind === "operator" ? ` (${t("hw.operator_sale")})` : ""}</dd>
          <dt>{t("hw.brand_model")}</dt><dd>{p.brand} / {p.model}</dd>
          <dt>{t("hw.in_box")}</dt><dd>{(p.in_box ?? []).join(", ") || "—"}</dd>
          <dt>{t("hw.warranty")}</dt><dd>{t("hw.months", { count: p.warranty_months })}</dd>
          <dt>{t("hw.assembly")}</dt><dd>{p.assembly_included ? t("common.yes") : t("common.no")}</dd>
          <dt>{t("hw.ships_from")}</dt><dd>{p.ship_from_country}</dd>
          <dt>{t("hw.destinations")}</dt><dd>{p.destinations?.length ? p.destinations.join(", ") : t("hw.by_rate")}</dd>
          <dt>{t("hw.prep")}</dt><dd>{t("hw.days", { count: p.prep_days + (p.made_to_order ? p.lead_time_days : 0) })}</dd>
          <dt>{t("hw.package")}</dt><dd>{p.skus[0] ? `${p.skus[0].weight_g / 1000} kg, ${p.skus[0].dims_mm.join("×")} mm` : "—"}</dd>
          <dt>{t("hw.freight")}</dt><dd>{p.freight_mode === "custom_quote" ? t("hw.custom_quote") : t("hw.parcel")}</dd>
          <dt>{t("hw.returns")}</dt><dd>{p.return_terms}</dd>
          <dt>{t("hw.delivery_terms")}</dt><dd>{p.delivery_terms}</dd>
          {Object.entries(p.specs ?? {}).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{Array.isArray(v) ? v.join(", ") : String(v)}</dd></Fragment>)}
        </dl>
        <p className="hint">{t("hw.compat_note")}</p>
      </div>
      {p.bom && p.kind === "kit_component" && <div style={{ gridColumn: "1 / -1" }}><BomBuilder slug={p.slug} app={app} /></div>}
      <div style={{ gridColumn: "1 / -1" }}><ReviewsSection slug={p.slug} kind="product" path={`${p.kind === "simple" ? "devices" : "kits"}/${p.slug}`} /></div>
    </div>
  );
}

