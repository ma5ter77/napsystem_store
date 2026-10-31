import Link from "next/link";
import { notFound } from "next/navigation";
import { isLocale, Locale, translator } from "@/lib/i18n";

const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export default async function Doc({ params }: { params: Promise<{ locale: string; doc: string }> }) {
  const { locale: l, doc } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = translator(locale);
  if (!/^[a-z0-9.-]+$/.test(doc)) notFound();
  const res = await fetch(`${BACKEND}/api/v1/about/documents/${doc}`, { cache: "no-store" });
  if (res.status === 404) notFound();
  const text = await res.text();
  return (
    <div className="section stack">
      <Link href={`/${locale}/about`}>{t("about.back")}</Link>
      {locale === "en" && <p className="hint">{t("about.doc_lang_note")}</p>}
      <article className="panel" lang="ru">
        <pre style={{ whiteSpace: "pre-wrap", font: "15px/1.6 var(--text)", margin: 0, maxWidth: "80ch" }}>{text}</pre>
      </article>
    </div>
  );
}
