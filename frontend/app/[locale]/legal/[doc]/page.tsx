import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Markdown } from "@/components/Markdown";
import { isLocale, Locale } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";

const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8000";
type P = Promise<{ locale: string; doc: string }>;

async function load(doc: string, locale: Locale) {
  if (!/^[a-z-]+$/.test(doc)) return null;
  const idx = await serverApi<any>("/legal", locale);
  const meta = idx.ok ? idx.data.documents.find((d: any) => d.key === doc) : null;
  if (!meta) return null;
  const lang = meta.languages.includes(locale) ? locale : meta.languages[0];
  const res = await fetch(`${BACKEND}/api/v1/legal/${doc}/${lang}`, { cache: "no-store" });
  if (!res.ok) return null;
  return { meta, lang, text: await res.text() };
}

export async function generateMetadata({ params }: { params: P }): Promise<Metadata> {
  const { locale: l, doc } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const d = await load(doc, locale);
  return { title: d ? (d.meta.title[locale] ?? d.meta.title.ru) : undefined, alternates: alternates(locale, `/legal/${doc}`) };
}

/** NAP-062: one legal document in the interface language (RU / EN), with its version and date. */
export default async function LegalDoc({ params }: { params: P }) {
  const { locale: l, doc } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = await serverTranslator(locale);
  const d = await load(doc, locale);
  if (!d) notFound();
  return (
    <div className="section stack">
      <Link href={`/${locale}/legal`}>{t("legal.back")}</Link>
      <article className="panel legal-doc" lang={d.lang}>
        <Markdown text={d.text} />
      </article>
      <p className="hint">{t("legal.version", { version: d.meta.version, date: new Date(d.meta.date).toLocaleDateString(locale) })}</p>
    </div>
  );
}
