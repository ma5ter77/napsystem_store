import type { Metadata } from "next";
import Link from "next/link";
import { ProductCard } from "@/components/cards";
import { isLocale, Locale, translator } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  return { title: (await serverTranslator(isLocale(locale) ? locale : "ru"))("devices.title"), alternates: alternates(locale, "/devices") };
}

export default async function Devices({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: Promise<Record<string, string | undefined>> }) {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = (await serverTranslator(locale));
  const sp = await searchParams;
  const qs = new URLSearchParams({ kind: "devices", limit: "100" });
  if (sp.category) qs.set("category", sp.category);
  if (sp.q) qs.set("q", sp.q);
  if (sp.min_rating) qs.set("min_rating", sp.min_rating);
  if (sp.sort) qs.set("sort", sp.sort);
  const [res, cats] = await Promise.all([serverApi<any>(`/hardware/products?${qs}`, locale),
                                         serverApi<any>("/categories?kind=hardware", locale)]);
  return (
    <div className="section">
      <h1>{t("devices.title")}</h1>
      <p className="muted">{t("devices.lead")}</p>
      <form className="filters" method="get" role="search">
        <label className="field">{t("catalog.search")}<input name="q" defaultValue={sp.q ?? ""} /></label>
        <label className="field">{t("catalog.rating")}
          <select name="min_rating" defaultValue={sp.min_rating ?? ""}><option value="">{t("catalog.any")}</option><option value="4">{t("catalog.rating_4")}</option></select></label>
        <label className="field">{t("catalog.sort")}
          <select name="sort" defaultValue={sp.sort ?? ""}><option value="">{t("catalog.sort_default")}</option><option value="rating">{t("catalog.sort_rating")}</option></select></label>
        {sp.category && <input type="hidden" name="category" value={sp.category} />}
        <div><button className="btn" type="submit">{t("catalog.apply")}</button></div>
      </form>
      <div className="chips" style={{ marginBottom: 22 }}>
        <Link className="chip" href={`/${locale}/devices`} aria-current={!sp.category}>{t("catalog.all_categories")}</Link>
        {cats.ok && cats.data.items.map((c: any) => (
          <Link key={c.slug} className="chip" href={`/${locale}/devices?category=${c.slug}`} aria-current={sp.category === c.slug}>{c.name}</Link>
        ))}
      </div>
      {!res.ok ? <div className="notice bad">{t("common.error")}</div> : res.data.items.length === 0 ?
        <div className="panel">{t("catalog.empty")}</div> :
        <div className="grid">{res.data.items.map((p: any) => <ProductCard key={p.id} p={p} locale={locale} t={t} />)}</div>}
    </div>
  );
}
