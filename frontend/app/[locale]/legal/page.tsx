import type { Metadata } from "next";
import Link from "next/link";
import { isLocale, Locale } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";

type P = Promise<{ locale: string }>;

export async function generateMetadata({ params }: { params: P }): Promise<Metadata> {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = await serverTranslator(locale);
  return { title: t("legal.title"), alternates: alternates(locale, "/legal") };
}

/** NAP-062: the list of legal documents with their current version. */
export default async function Legal({ params }: { params: P }) {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = await serverTranslator(locale);
  const r = await serverApi<any>("/legal", locale);
  const docs: any[] = r.ok ? r.data.documents : [];
  return (
    <div className="section stack">
      <h1>{t("legal.title")}</h1>
      {r.ok && r.data.status === "review" && <div className="notice">{t("legal.review_note")}</div>}
      <ul className="stack plain">
        {docs.map((d) => (
          <li key={d.key} className="panel flat">
            <Link href={`/${locale}/legal/${d.key}`}><strong>{d.title[locale] ?? d.title.ru}</strong></Link>
            <div className="hint">{t("legal.version", { version: d.version, date: new Date(d.date).toLocaleDateString(locale) })}</div>
          </li>))}
      </ul>
    </div>
  );
}
