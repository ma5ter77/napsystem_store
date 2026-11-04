"use client";

// NAP-116: one picture of "which version is where" and "what changes" for moderators, admins and authors.
// VersionTrack: the version buyers get now → the version being reviewed or edited.
// ChangeList: every difference, grouped; texts shown word by word ("было → стало").
// StorePreview: the store card as the buyer will see it, built from the texts being reviewed or edited.
import { AppCover } from "@/components/Art";
import { useApp } from "@/components/Providers";
import { wordDiff } from "@/lib/diff";
import { date } from "@/lib/i18n";
import { useState } from "react";

export type Change = { area: string; kind: "added" | "removed" | "changed"; key?: string; locale?: string; before: any; after: any };

export function DiffText({ before, after, lang }: { before: string; after: string; lang?: string }) {
  const { t } = useApp();
  const parts = wordDiff(before ?? "", after ?? "");
  return (
    <div className="diff" lang={lang}>
      <div className="diff-side is-before">
        <span className="diff-label">{t("chg.before")}</span>
        <p>{before ? parts.filter((p) => p.op !== "ins").map((p, i) => p.op === "del" ? <del key={i}>{p.text}</del> : <span key={i}>{p.text}</span>) : <em className="hint">{t("chg.empty")}</em>}</p>
      </div>
      <div className="diff-side is-after">
        <span className="diff-label">{t("chg.after")}</span>
        <p>{after ? parts.filter((p) => p.op !== "del").map((p, i) => p.op === "ins" ? <ins key={i}>{p.text}</ins> : <span key={i}>{p.text}</span>) : <em className="hint">{t("chg.empty")}</em>}</p>
      </div>
    </div>
  );
}

type Ver = { label: string; at?: string | null; note: string };

export function VersionTrack({ live, next, sameAsLive }: { live: Ver | null; next: Ver | null; sameAsLive?: boolean }) {
  const { t, locale } = useApp();
  const stop = (v: Ver | null, cls: string, title: string) => (
    <div className={`vt-stop ${cls}`}>
      <span className="vt-dot" aria-hidden="true" />
      <span className="vt-title">{title}</span>
      {v ? <span className="vt-ver">v{v.label}</span> : <span className="vt-ver is-none">{t("chg.none")}</span>}
      {v && <span className="hint">{v.note}{v.at ? ` ${date(v.at, locale, true)}` : ""}</span>}
    </div>
  );
  return (
    <div className="version-track" data-testid="version-track" role="group" aria-label={t("chg.track_label")}>
      {stop(live, "is-live", t("chg.in_store"))}
      {!sameAsLive && <><span className="vt-line" aria-hidden="true" />{stop(next, "is-next", t("chg.reviewing"))}</>}
      {sameAsLive && <p className="hint vt-same">{t("chg.same_as_live")}</p>}
    </div>
  );
}

const GROUP: Record<string, "texts" | "program" | "other"> = {
  text: "texts", cover: "texts", session: "program", session_title: "program", session_sound: "program",
  layout: "program", order: "program", questionnaire: "other", equipment: "other",
};

function title(x: any, locale: string) { return x ? (x[locale] ?? Object.values(x)[0] ?? "") : ""; }

function Line({ c }: { c: Change }) {
  const { t, locale } = useApp();
  const badge = <span className={`chg-kind is-${c.kind}`}>{t(`chg.kind.${c.kind}`)}</span>;
  switch (c.area) {
    case "text":
      return <li className="chg-item">{badge}<strong>{t(`mm.app_${c.key}`)}</strong> <span className="hint">· {c.locale?.toUpperCase()}</span>
        <DiffText before={c.before} after={c.after} lang={c.locale} /></li>;
    case "cover":
      return <li className="chg-item">{badge}<strong>{t("chg.cover")}</strong></li>;
    case "session":
      return <li className="chg-item">{badge}<strong>{t("chg.session", { title: title(c.after ?? c.before, locale), key: c.key ?? "" })}</strong></li>;
    case "session_title":
      return <li className="chg-item">{badge}<strong>{t("chg.session_title", { key: c.key ?? "" })}</strong>
        <DiffText before={title(c.before, locale)} after={title(c.after, locale)} /></li>;
    case "session_sound":
      return <li className="chg-item">{badge}<strong>{t("chg.session_sound", { key: c.key ?? "" })}</strong>
        <span className="hint"> {t("chg.length", { before: Math.round(c.before / 60 * 10) / 10, after: Math.round(c.after / 60 * 10) / 10 })}</span></li>;
    case "layout":
      return <li className="chg-item">{badge}<strong>{t(`chg.layout_${c.kind}`, { layout: t(`layout.${c.after ?? c.before}`), key: c.key ?? "" })}</strong></li>;
    case "order":
      return <li className="chg-item">{badge}<strong>{t("chg.order")}</strong> <span className="hint">{(c.before as string[]).join(" · ")} → {(c.after as string[]).join(" · ")}</span></li>;
    case "questionnaire":
      return <li className="chg-item">{badge}<strong>{t(`chg.${c.key}`)}</strong> <span className="hint">{c.before ?? "—"} → {c.after ?? "—"}</span></li>;
    case "equipment":
      return <li className="chg-item">{badge}<strong>{t("chg.equipment")}</strong>
        <span className="hint"> {(c.before as string[]).map((x) => t(`caps.${x}`)).join(", ") || "—"} → {(c.after as string[]).map((x) => t(`caps.${x}`)).join(", ") || "—"}</span></li>;
    default:
      return null;
  }
}

export function ChangeList({ changes, empty }: { changes: Change[]; empty: string }) {
  const { t } = useApp();
  if (!changes.length) return <p className="hint" data-testid="changes-empty">{empty}</p>;
  const groups = (["texts", "program", "other"] as const).map((g) => [g, changes.filter((c) => (GROUP[c.area] ?? "other") === g)] as const).filter(([, xs]) => xs.length);
  return (
    <div className="stack" data-testid="changes">
      {groups.map(([g, xs]) => (
        <section key={g} className="chg-group">
          <h3>{t(`chg.group.${g}`)} <span className="hint">{xs.length}</span></h3>
          <ul className="chg-list">{xs.map((c, i) => <Line key={i} c={c} />)}</ul>
        </section>))}
    </div>
  );
}

/** The changes between two sets of store texts {locale: {title, subtitle, description}} (for editors, before saving). */
export function textChanges(before: any, after: any, fields: readonly string[] = ["title", "subtitle", "description"]): Change[] {
  const out: Change[] = [];
  for (const loc of Array.from(new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])).sort()) {
    for (const f of fields) {
      const b = (before?.[loc]?.[f] ?? "").trim(), a = (after?.[loc]?.[f] ?? "").trim();
      if (b !== a) out.push({ area: "text", key: f, locale: loc, kind: !b ? "added" : !a ? "removed" : "changed", before: b, after: a });
    }
  }
  return out;
}

/** WYSIWYG: the top of the store card, as the buyer will see it, from the texts given. */
export function StorePreview({ texts, sessions = [], slug, hue = 210, coverUrl, author }: {
  texts: Record<string, { title?: string; subtitle?: string; description?: string } | undefined>;
  sessions?: { key: string; titles: Record<string, string> }[]; slug: string; hue?: number; coverUrl?: string | null; author?: string;
}) {
  const { t, locale } = useApp();
  const langs = Object.keys(texts).filter((l) => texts[l]?.title);
  const [lang, setLang] = useState(langs.includes(locale) ? locale : langs[0] ?? locale);
  const x = texts[lang] ?? {};
  return (
    <figure className="store-preview" data-testid="store-preview">
      <figcaption className="sp-bar">
        <span>{t("chg.preview_caption")}</span>
        {langs.length > 1 && <span className="seg" role="group" aria-label={t("chg.preview_lang")}>
          {langs.map((l) => <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)}>{l.toUpperCase()}</button>)}</span>}
      </figcaption>
      <div className="sp-body" lang={lang}>
        <div className="sp-art"><AppCover a={{ slug, cover_hue: hue, cover_url: coverUrl ?? null, title: x.title }} /></div>
        <div className="sp-text">
          <h2>{x.title || <span className="hint">{t("chg.no_title")}</span>}</h2>
          {x.subtitle && <p className="muted">{x.subtitle}</p>}
          {author && <p className="hint">{t("app.author")}: {author}</p>}
          {sessions.length > 0 && <ol className="steps">{sessions.map((s) => <li key={s.key}>{s.titles[lang] ?? Object.values(s.titles)[0]}</li>)}</ol>}
          {x.description && <p className="sp-desc">{x.description}</p>}
        </div>
      </div>
    </figure>
  );
}
