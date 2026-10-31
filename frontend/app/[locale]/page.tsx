import Link from "next/link";
import { BeatCover } from "@/components/Art";
import { AppCard, ProductCard } from "@/components/cards";
import { isLocale, Locale, translator } from "@/lib/i18n";
import { serverApi, serverTranslator } from "@/lib/server";
import { ServerError } from "@/components/State";

export default async function Home({ params }: { params: Promise<{ locale: string }> }) {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = (await serverTranslator(locale));
  const [apps, kits, devices] = await Promise.all([
    serverApi<any>("/catalog/apps?limit=6", locale),
    serverApi<any>("/hardware/products?kind=kits&limit=3", locale),
    serverApi<any>("/hardware/products?kind=devices&limit=4", locale),
  ]);
  return (
    <>
      <section className="hero">
        <div>
          <h1>{t("home.title")}</h1>
          <p className="muted" style={{ fontSize: "1.1rem" }}>{t("home.lead")}</p>
          <div className="row">
            <Link className="btn" href={`/${locale}/apps`}>{t("home.cta_apps")}</Link>
            <Link className="btn ghost" href={`/${locale}/developer`}>{t("home.cta_dev")}</Link>
          </div>
        </div>
        <div className="hero-art" aria-hidden="true"><BeatCover hue={205} seed="nap-hero" beats={4} /></div>
      </section>
      <p className="hint" style={{ marginTop: -8 }}>{t("home.art_note")}</p>
      <section className="section">
        <div className="spread"><h2>{t("home.apps")}</h2><Link href={`/${locale}/apps`}>{t("common.all")}</Link></div>
        {apps.ok ? (
          <div className="grid">{apps.data.items.map((a: any) => <AppCard key={a.id} a={a} locale={locale} t={t} />)}</div>
        ) : <ServerError t={t} locale={locale} status={apps.status} />}
      </section>
      <section className="section">
        <div className="spread"><h2>{t("home.kits")}</h2><Link href={`/${locale}/kits`}>{t("common.all")}</Link></div>
        {kits.ok && <div className="grid">{kits.data.items.map((p: any) => <ProductCard key={p.id} p={p} locale={locale} t={t} />)}</div>}
      </section>
      <section className="section">
        <div className="spread"><h2>{t("home.devices")}</h2><Link href={`/${locale}/devices`}>{t("common.all")}</Link></div>
        {devices.ok && <div className="grid">{devices.data.items.map((p: any) => <ProductCard key={p.id} p={p} locale={locale} t={t} />)}</div>}
      </section>
    </>
  );
}
