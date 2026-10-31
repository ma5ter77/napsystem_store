import Link from "next/link";
import { duration, langName, Locale, money, num1, T } from "@/lib/i18n";
import { BeatCover, ProductArt } from "./Art";

export function priceLabel(prices: any[], t: T, locale: Locale): string {
  if (!prices?.length) return t("app.not_for_sale");
  const p = prices[0];
  if (p.kind === "free") return t("app.free");
  if (p.kind === "subscription") return t(`app.per_${p.interval}`, { price: money(p.amount_minor, p.currency, locale) });
  return money(p.amount_minor, p.currency, locale);
}

export function AppCard({ a, locale, t }: { a: any; locale: Locale; t: T }) {
  return (
    <Link className="card" href={`/${locale}/apps/${a.slug}`}>
      <div className="art"><BeatCover hue={a.cover_hue} seed={a.slug} title={a.title} /></div>
      <h3 lang={a.lang ?? undefined}>{a.title ?? a.slug}</h3>
      {a.fallback && a.lang && <span className="badge gray">{t("common.original_lang", { lang: langName(a.lang, locale) })}</span>}
      <div className="meta">{a.author} · {t("app.sessions", { count: a.sessions_count })} · {duration(a.total_duration_seconds, locale)}</div>
      {a.rating?.average != null && <div className="meta" aria-label={t("rev.card", { avg: num1(a.rating.average, locale), count: a.rating.count })}><span className="stars-inline" aria-hidden="true">★</span> {num1(a.rating.average, locale)} <span aria-hidden="true">({a.rating.count})</span></div>}
      <div className="meta"><span className="price">{priceLabel(a.prices, t, locale)}</span>{a.content_language && a.content_language !== "none" ? ` · ${t("app.audio_lang")}: ${langName(a.content_language, locale)}` : ""}</div>
    </Link>
  );
}

export function ProductCard({ p, locale, t }: { p: any; locale: Locale; t: T }) {
  const sku = p.skus?.[0];
  const av = sku?.availability;
  const href = p.kind === "simple" ? `/${locale}/devices/${p.slug}` : `/${locale}/kits/${p.slug}`;
  return (
    <Link className="card" href={href}>
      <div className="art"><ProductArt category={p.category.slug} hue={p.hue} title={p.title} /></div>
      <h3 lang={p.lang ?? undefined}>{p.title}</h3>
      <div className="meta">{p.seller.name}</div>
      {p.rating?.average != null && <div className="meta" aria-label={t("rev.card", { avg: num1(p.rating.average, locale), count: p.rating.count })}><span className="stars-inline" aria-hidden="true">★</span> {num1(p.rating.average, locale)} <span aria-hidden="true">({p.rating.count})</span></div>}
      <div className="meta">
        {p.price_from_minor !== null ? <span className="price">{money(p.price_from_minor, p.currency, locale)}</span> : t("kit.from_components")}
        {av && <> · {availabilityText(av, t)}</>}
      </div>
    </Link>
  );
}

export function availabilityText(av: any, t: T): string {
  if (!av) return "";
  if (av.type === "made_to_order") return t("hw.made_to_order", { days: av.lead_time_days });
  if (av.type === "out_of_stock") return t("hw.out_of_stock");
  if (av.last_unit) return t("hw.last_unit");
  return t("hw.in_stock", { count: av.available });
}

export function CompatBadge({ status, t }: { status: string; t: T }) {
  const cls = status === "verified" ? "ok" : status === "incompatible" ? "bad" : status === "vendor_claimed" ? "warn" : "gray";
  return <span className={`badge ${cls}`}>{t(`compat.${status}`)}</span>;
}
