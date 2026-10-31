import type { Metadata } from "next";
import Link from "next/link";
import { AppCard } from "@/components/cards";
import { isLocale, langName, Locale, translator } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";

type SP = Promise<Record<string, string | undefined>>;

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  const t = (await serverTranslator(isLocale(locale) ? locale : "ru"));
  return { title: t("catalog.title"), alternates: alternates(locale, "/apps") };
}

export default async function Catalog({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: SP }) {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = (await serverTranslator(locale));
  const sp = await searchParams;
  const qs = new URLSearchParams();
  for (const k of ["q", "category", "price", "content_language", "min_rating", "sort"]) if (sp[k]) qs.set(k, sp[k]!);
  const [res, cats] = await Promise.all([serverApi<any>(`/catalog/apps?limit=60&${qs}`, locale),
                                         serverApi<any>("/categories?kind=app", locale)]);
  const link = (k: string, v?: string) => {
    const n = new URLSearchParams(qs);
    if (v) n.set(k, v); else n.delete(k);
    return `/${locale}/apps${n.toString() ? "?" + n : ""}`;
  };
  return (
    <div className="section">
      <h1>{t("catalog.title")}</h1>
      <form className="filters" method="get" role="search">
        <label className="field">{t("catalog.search")}
          <input name="q" defaultValue={sp.q ?? ""} placeholder={t("catalog.search_ph")} /></label>
        <label className="field">{t("catalog.price")}
          <select name="price" defaultValue={sp.price ?? ""}>
            <option value="">{t("catalog.any")}</option>
            <option value="free">{t("app.free")}</option>
            <option value="one_time">{t("catalog.one_time")}</option>
            <option value="subscription">{t("catalog.subscription")}</option>
          </select></label>
        <label className="field">{t("app.audio_lang")}
          <select name="content_language" defaultValue={sp.content_language ?? ""}>
            <option value="">{t("catalog.any")}</option>
            {["none", "ru", "en"].map((c) => <option key={c} value={c}>{langName(c, locale)}</option>)}
          </select></label>
        <label className="field">{t("catalog.rating")}
          <select name="min_rating" defaultValue={sp.min_rating ?? ""}>
            <option value="">{t("catalog.any")}</option>
            <option value="4">{t("catalog.rating_4")}</option>
          </select></label>
        <label className="field">{t("catalog.sort")}
          <select name="sort" defaultValue={sp.sort ?? ""}>
            <option value="">{t("catalog.sort_default")}</option>
            <option value="rating">{t("catalog.sort_rating")}</option>
            <option value="new">{t("catalog.sort_new")}</option>
          </select></label>
        {sp.category && <input type="hidden" name="category" value={sp.category} />}
        <div><button className="btn" type="submit">{t("catalog.apply")}</button></div>
      </form>
      <div className="chips" style={{ marginBottom: 22 }}>
        <Link className="chip" href={link("category")} aria-current={!sp.category}>{t("catalog.all_categories")}</Link>
        {cats.ok && cats.data.items.map((c: any) => (
          <Link key={c.slug} className="chip" href={link("category", c.slug)} aria-current={sp.category === c.slug}>{c.name}</Link>
        ))}
      </div>
      {!res.ok ? <div className="notice bad">{t("common.error")}</div> :
        res.data.items.length === 0 ? <div className="panel"><p>{t("catalog.empty")}</p><Link href={`/${locale}/apps`}>{t("catalog.reset")}</Link></div> :
          <>
            <p className="muted">{t("catalog.found", { count: res.data.total })}</p>
            <div className="grid">{res.data.items.map((a: any) => <AppCard key={a.id} a={a} locale={locale} t={t} />)}</div>
          </>}
    </div>
  );
}
