import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { BeatCover } from "@/components/Art";
import { priceLabel } from "@/components/cards";
import { AppActions } from "@/components/AppActions";
import { ViewBeacon } from "@/components/ViewBeacon";
import { ReviewsSection, Stars } from "@/components/Reviews";
import { duration, isLocale, langName, Locale, num1, translator } from "@/lib/i18n";
import { alternates, serverApi, serverTranslator } from "@/lib/server";

type P = Promise<{ locale: string; slug: string }>;

export async function generateMetadata({ params }: { params: P }): Promise<Metadata> {
  const { locale, slug } = await params;
  const r = await serverApi<any>(`/catalog/apps/${slug}`, locale);
  if (!r.ok) return {};
  return { title: r.data.title, description: r.data.subtitle, alternates: alternates(locale, `/apps/${slug}`) };
}

export default async function AppPage({ params }: { params: P }) {
  const { locale: l, slug } = await params;
  const locale = (isLocale(l) ? l : "ru") as Locale;
  const t = (await serverTranslator(locale));
  const r = await serverApi<any>(`/catalog/apps/${slug}`, locale);
  if (!r.ok) {
    if (r.status === 404) notFound();
    return <div className="section notice bad">{t("common.error")}</div>;
  }
  const a = r.data;
  const price = a.prices?.[0];
  const caps: string[] = a.requirements?.required_capabilities ?? [];
  return (
    <div className="section two">
      <ViewBeacon slug={a.slug} />
      <div className="stack">
        <div className="hero-art" style={{ aspectRatio: "4/3" }}><BeatCover hue={a.cover_hue} seed={a.slug} title={a.title} /></div>
        <div className="panel stack">
          <h2>{t("app.program")}</h2>
          <ol className="steps">
            {a.program.map((s: any) => (
              <li key={s.key}><span lang={s.title_lang}>{s.title}</span> <span className="muted">— {duration(s.duration_seconds, locale)}</span></li>
            ))}
          </ol>
          {a.dsp_profile && <p className="hint">{t("app.dsp_note", { profile: a.dsp_profile.profile, v: a.dsp_profile.profile_version })}</p>}
        </div>
      </div>
      <div className="stack">
        <div>
          <h1 lang={a.lang ?? undefined}>{a.title}</h1>
          {a.fallback && a.lang && <span className="badge gray">{t("common.original_lang", { lang: langName(a.lang, locale) })}</span>}
          <p className="muted" lang={a.lang ?? undefined}>{a.subtitle}</p>
          {a.rating?.average != null && <a href="#reviews-h" className="row hint"><Stars value={a.rating.average} label={t("rev.card", { avg: num1(a.rating.average, locale), count: a.rating.count })} /> {num1(a.rating.average, locale)} · {t("rev.ratings_n", { count: a.rating.count })}</a>}
        </div>
        <dl className="kv">
          <dt>{t("app.author")}</dt><dd>{a.author}</dd>
          <dt>{t("app.category")}</dt><dd>{a.category?.name ?? "—"}</dd>
          <dt>{t("app.length")}</dt><dd>{t("app.sessions", { count: a.sessions_count })}, {duration(a.total_duration_seconds, locale)}</dd>
          <dt>{t("app.audio_lang")}</dt><dd>{langName(a.content_language, locale)}</dd>
          <dt>{t("app.version")}</dt><dd>{a.version}</dd>
          <dt>{t("app.compat")}</dt><dd>{caps.map((c) => t(`caps.${c}`)).join(", ") || "—"}</dd>
          <dt>{t("app.price")}</dt><dd><strong>{priceLabel(a.prices, t, locale)}</strong></dd>
        </dl>
        {price?.kind === "subscription" && <p className="hint">{t("app.sub_terms")}</p>}
        <AppActions app={a} />
        <div className="panel flat">
          <h3>{t("app.equipment")}</h3>
          <p className="hint">{t("app.equipment_hint")}</p>
          <Link className="btn ghost" href={`/${locale}/kits?app=${a.slug}`}>{t("app.pick_equipment")}</Link>
        </div>
        <div lang={a.lang ?? undefined}>{(a.description ?? "").split("\n").map((p: string, i: number) => <p key={i}>{p}</p>)}</div>
        <p className="hint">{t("app.no_promise")}</p>
      </div>
      <div style={{ gridColumn: "1 / -1" }}><ReviewsSection slug={a.slug} /></div>
    </div>
  );
}
