import type { Metadata } from "next";
import Link from "next/link";
import { date, isLocale, Locale, translator } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  return { title: (await serverTranslator(isLocale(locale) ? locale : "ru"))("about.title"), alternates: alternates(locale, "/about") };
}

const TONE: Record<string, string> = { done: "ok", partial: "warn", simulated: "r", not_started: "gray" };

export default async function About({ params }: { params: Promise<{ locale: string }> }) {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = (await serverTranslator(locale));
  const r = await serverApi<any>("/version", locale);
  if (!r.ok) return <div className="section notice bad">{t("common.error")}</div>;
  const v = r.data;
  const specDocs = v.documents.filter((d: string) => d.startsWith("spec-"));
  return (
    <div className="section stack">
      <h1>{t("about.title")}</h1>
      <p className="muted">{t("about.lead")}</p>
      <div className="two">
        <section className="panel stack">
          <dl className="kv">
            <dt>{t("about.product")}</dt><dd><strong>{v.version}</strong></dd>
            <dt>{t("about.spec")}</dt><dd><strong>v{v.spec.version}</strong> · {v.spec.status} · {date(v.spec.date, locale)}</dd>
            <dt>{t("about.stage")}</dt><dd>{v.stage}</dd>
            <dt>{t("about.released")}</dt><dd>{date(v.released, locale)}</dd>
            <dt>{t("about.description")}</dt><dd>v{v.description_version}</dd>
            <dt>{t("about.environment")}</dt><dd>{v.environment}</dd>
            <dt>{t("about.build")}</dt><dd>{v.build}</dd>
          </dl>
        </section>
        <section className="panel stack">
          <h2>{t("about.acceptance", { spec: v.spec.version })}</h2>
          <p>{t("about.acc_line", v.acceptance)}</p>
          <ul className="hint">{v.acceptance.not_run_items.map((x: string) => <li key={x}>{x}</li>)}</ul>
          {v.backlog_url && (
            <div className="stack">
              <h3>{t("about.backlog")}</h3>
              <p className="hint">{t("about.backlog_hint")}</p>
              <a className="btn ghost" href={v.backlog_url} target="_blank" rel="noreferrer">{t("about.backlog")}</a>
            </div>
          )}
        </section>
      </div>
      <section className="panel stack">
        <h2>{t("about.coverage")}</h2>
        <div className="scroll-x"><table>
          <thead><tr><th>{t("about.section")}</th><th>{t("about.status")}</th><th>{t("about.note")}</th></tr></thead>
          <tbody>{v.coverage.map((c: any) => (
            <tr key={c.section}>
              <td>{c.section}. {c.title[locale]}</td>
              <td><span className={`badge ${TONE[c.status] ?? "gray"}`}>{t(`coverage.${c.status}`)}</span></td>
              <td className="hint">{c.note[locale]}</td>
            </tr>))}
          </tbody>
        </table></div>
      </section>
      <div className="two">
        <section className="panel stack">
          <h2>{t("about.releases")}</h2>
          <ol className="steps">{v.releases.map((x: any) => (
            <li key={x.version}><strong>{x.version}</strong> · {date(x.date, locale)} · {t("about.implements", { spec: x.spec })}<div className="hint">{x.summary[locale]}</div></li>))}
          </ol>
        </section>
        <section className="panel stack">
          <h2>{t("about.documents")}</h2>
          <ul className="steps">
            {specDocs.map((d: string) => { const sv = d.replace("spec-", ""); const draft = Number(sv) > Number(v.spec.version);
              return <li key={d}><Link href={`/${locale}/about/${d}`}>{t("about.doc_spec", { v: sv })}</Link>{draft ? ` · ${t("about.spec_draft")}` : sv === v.spec.version ? ` · ${t("about.spec_current")}` : ""}</li>; })}
            <li><Link href={`/${locale}/about/project`}>{t("about.doc_project", { v: v.description_version })}</Link></li>
            <li><Link href={`/${locale}/about/changelog`}>{t("about.doc_changelog")}</Link></li>
          </ul>
          {locale === "en" && <p className="hint">{t("about.doc_lang_note")}</p>}
        </section>
      </div>
    </div>
  );
}
