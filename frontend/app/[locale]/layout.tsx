import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import "../globals.css";
import { Header } from "@/components/Header";
import { Providers } from "@/components/Providers";
import { isLocale, translator } from "@/lib/i18n";
import { alternates, serverApi } from "@/lib/server";
import Link from "next/link";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const t = translator(locale);
  return { title: { default: "NAP Store", template: "%s — NAP Store" }, description: t("home.meta"),
           alternates: alternates(locale, "") };
}

export const dynamic = "force-dynamic";


export default async function LocaleLayout({ children, params }: { children: React.ReactNode; params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = translator(locale);
  const rel = await serverApi<any>("/version", locale);
  return (
    <html lang={locale}>
      <body>
        <Providers locale={locale}>
          <Suspense><Header /></Suspense>
          <main className="wrap" id="main">{children}</main>
          <footer className="foot">
            <div className="wrap stack">
              <p>{t("footer.disclaimer")}</p>
              <p>{t("footer.methods")}</p>
              {rel.ok && <p><Link href={`/${locale}/about`}>{t("footer.version", { version: rel.data.version, spec: rel.data.spec.version })}</Link></p>}
            </div>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
