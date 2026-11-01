import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import "@fontsource-variable/inter/index.css";
import "../globals.css";
import { Header } from "@/components/Header";
import { Providers } from "@/components/Providers";
import { CurrencySwitch } from "@/components/CurrencySwitch";
import { cookies, headers } from "next/headers";
import { isLocale, translator } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";
import Link from "next/link";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const t = (await serverTranslator(locale));
  return { title: { default: "NAP Store", template: "%s — NAP Store" }, description: t("home.meta"),
           alternates: alternates(locale, "") };
}

export const dynamic = "force-dynamic";


export default async function LocaleLayout({ children, params }: { children: React.ReactNode; params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = (await serverTranslator(locale));
  const rel = await serverApi<any>("/version", locale);
  const jar = await cookies();
  const hdr = (await headers()).get("accept-language") ?? "";
  const region = (hdr.split(",")[0].split(";")[0].split("-")[1] ?? "").toUpperCase();  // same rule as the API
  const currencies = (process.env.NAP_CURRENCIES || "EUR,USD").replace(/[[\]"\s]/g, "").split(",").map((c) => c.trim().toUpperCase()).filter(Boolean);
  const cur = ((c) => (c && currencies.includes(c) ? c : ["US", "PR", "EC", "SV", "PA", "TL", "FM", "MH", "PW", "ZW"].includes(region) ? "USD" : region === "RU" && currencies.includes("RUB") ? "RUB" : "EUR"))(jar.get("nap_currency")?.value);
  return (
    <html lang={locale}>
      <body>
        <Providers locale={locale}>
          <a className="skip-link" href="#main">{t("nav.skip")}</a>
          <Suspense><Header /></Suspense>
          <main className="wrap" id="main" tabIndex={-1}>{children}</main>
          <footer className="foot">
            <div className="wrap">
              <p>{t("footer.disclaimer")}</p>
              <details><summary>{t("footer.methods_more")}</summary><p>{t("footer.methods")}</p></details>
              <nav className="foot-cols" aria-label={t("footer.col_about")}>
                <div><h2>{t("footer.col_shop")}</h2><ul>
                  <li><Link href={`/${locale}/apps`}>{t("nav.apps")}</Link></li>
                  <li><Link href={`/${locale}/devices`}>{t("nav.devices")}</Link></li>
                  <li><Link href={`/${locale}/kits`}>{t("nav.kits")}</Link></li></ul></div>
                <div><h2>{t("footer.col_account")}</h2><ul>
                  <li><Link href={`/${locale}/library`}>{t("nav.library")}</Link></li>
                  <li><Link href={`/${locale}/orders`}>{t("nav.orders")}</Link></li>
                  <li><Link href={`/${locale}/account`}>{t("nav.account")}</Link></li></ul></div>
                <div><h2>{t("footer.col_authors")}</h2><ul>
                  <li><Link href={`/${locale}/developer`}>{t("footer.publish")}</Link></li>
                  <li><Link href={`/${locale}/seller`}>{t("footer.sell")}</Link></li></ul></div>
                <div><h2>{t("footer.col_about")}</h2><ul>
                  <li><Link href={`/${locale}/legal`}>{t("footer.legal")}</Link></li>
                  <li><Link href={`/${locale}/legal/terms`}>{t("legal.terms")}</Link></li>
                  <li><Link href={`/${locale}/legal/privacy`}>{t("legal.privacy")}</Link></li>
                  <li><Link href={`/${locale}/about`}>{t("footer.about")}</Link></li></ul></div>
              </nav>
              <div className="foot-base">
                <span>{t("footer.copyright", { year: new Date().getFullYear() })}</span>
                <span className="row">{t("footer.currency")}: <CurrencySwitch current={cur} options={currencies} /></span>
                {rel.ok && <Link href={`/${locale}/about`}>{t("footer.version", { version: rel.data.version, spec: rel.data.spec.version })}</Link>}
              </div>
            </div>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
