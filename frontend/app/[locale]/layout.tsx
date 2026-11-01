import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import "../globals.css";
import { Header } from "@/components/Header";
import { Providers } from "@/components/Providers";
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
  return (
    <html lang={locale}>
      <body>
        <Providers locale={locale}>
          <a className="skip-link" href="#main">{t("nav.skip")}</a>
          <Suspense><Header /></Suspense>
          <main className="wrap" id="main" tabIndex={-1}>{children}</main>
          <footer className="foot">
            <div className="wrap stack">
              <p>{t("footer.disclaimer")}</p>
              <p>{t("footer.methods")}</p>
              <p className="row foot-legal"><Link href={`/${locale}/legal`}>{t("footer.legal")}</Link>
                <Link href={`/${locale}/legal/terms`}>{t("legal.terms")}</Link>
                <Link href={`/${locale}/legal/privacy`}>{t("legal.privacy")}</Link></p>
              {rel.ok && <p><Link href={`/${locale}/about`}>{t("footer.version", { version: rel.data.version, spec: rel.data.spec.version })}</Link></p>}
            </div>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
