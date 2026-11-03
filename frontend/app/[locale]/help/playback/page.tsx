import type { Metadata } from "next";
import { PlaybackCheck } from "@/components/PlaybackCheck";
import { Breadcrumbs, PageHead } from "@/components/nav";
import { isLocale, Locale } from "@/lib/i18n";
import { alternates, serverTranslator } from "@/lib/server";

// NAP-109: how to listen — browsers, headphones, speakers 3.0 and the NAP chair 5.1 / 7.1 — with a live check of
// the visitor's own browser output.
export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  return { title: (await serverTranslator(isLocale(locale) ? locale : "ru"))("help.title"), alternates: alternates(locale, "/help/playback") };
}

export default async function PlaybackHelp({ params }: { params: Promise<{ locale: string }> }) {
  const { locale: l } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = await serverTranslator(locale);
  const list = (k: string, n: number) => <ul>{Array.from({ length: n }, (_, i) => <li key={i}>{t(`${k}_${i + 1}`)}</li>)}</ul>;
  return (
    <div className="section stack help-page">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { label: t("help.title") }]} />
      <PageHead title={t("help.title")} lead={t("help.lead")} />
      <PlaybackCheck />
      <div className="two">
        <section className="panel stack" aria-labelledby="h-hp">
          <h2 id="h-hp">{t("help.hp_title")}</h2>
          <p>{t("help.hp_text")}</p>
          {list("help.hp", 3)}
        </section>
        <section className="panel stack" aria-labelledby="h-multi">
          <h2 id="h-multi">{t("help.multi_title")}</h2>
          <p>{t("help.multi_text")}</p>
          {list("help.multi", 4)}
        </section>
      </div>
      <section className="panel stack" aria-labelledby="h-setup">
        <h2 id="h-setup">{t("help.setup_title")}</h2>
        <ol>{[1, 2, 3].map((i) => <li key={i}>{t(`help.setup_${i}`)}</li>)}</ol>
        <p className="hint">{t("help.setup_note")}</p>
      </section>
      <section className="panel stack" aria-labelledby="h-off">
        <h2 id="h-off">{t("help.offline_title")}</h2>
        <p>{t("help.offline_text")}</p>
      </section>
    </div>
  );
}
