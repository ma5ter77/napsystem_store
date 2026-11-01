import type { Metadata } from "next";
import { PageHead } from "@/components/nav";
import Link from "next/link";
import { ProductCard } from "@/components/cards";
import { isLocale, Locale, translator } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";
import { ServerError } from "@/components/State";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  return { title: (await serverTranslator(isLocale(locale) ? locale : "ru"))("kits.title"), alternates: alternates(locale, "/kits") };
}

export default async function Kits({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: Promise<Record<string, string | undefined>> }) {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = (await serverTranslator(locale));
  const { app } = await searchParams;
  if (app) {
    const [r, a] = await Promise.all([serverApi<any>(`/hardware/compatible?app=${encodeURIComponent(app)}`, locale),
                                      serverApi<any>(`/catalog/apps/${encodeURIComponent(app)}`, locale)]);
    return (
      <div className="section">
        <h1>{t("kits.for_app", { app: a.ok ? a.data.title : app })}</h1>
        {a.ok && <p className="muted">{t("kits.requires")}: {(a.data.requirements.required_capabilities ?? []).map((c: string) => t(`caps.${c}`)).join(", ")}</p>}
        <div className="notice">{t("kits.license_note")}</div>
        {!r.ok ? <ServerError t={t} locale={locale} status={r.status} /> : (
          <div className="grid" style={{ marginTop: 20 }}>
            {r.data.items.map((p: any) => (
              <div key={p.id}>
                <ProductCard p={{ ...p }} locale={locale} t={t} />
                <div style={{ marginTop: 6 }}>
                  {p.covers_requirements ? <span className="badge ok">{t("kits.covers")}</span> :
                    <span className="badge warn">{t("kits.missing")}: {p.unmet_capabilities.map((c: string) => t(`caps.${c}`)).join(", ")}</span>}
                  {" "}<Link href={`/${locale}/kits/${p.slug}?app=${app}`}>{t("kits.configure")}</Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }
  const r = await serverApi<any>("/hardware/products?kind=kits", locale);
  return (
    <div className="section">
      <PageHead title={t("kits.title")} lead={t("kits.lead")} />
      <p className="muted">{t("kits.lead")}</p>
      {!r.ok ? <ServerError t={t} locale={locale} status={r.status} /> :
        <div className="grid">{r.data.items.map((p: any) => <ProductCard key={p.id} p={p} locale={locale} t={t} />)}</div>}
    </div>
  );
}
