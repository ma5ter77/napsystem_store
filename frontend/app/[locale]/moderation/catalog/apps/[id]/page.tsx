"use client";

// NAP-084: the moderator edits store texts and category of an app, stops/resumes sales, deletes and restores it.
// Sound, program and price stay with the author (a new version / the author's price tab).
import { Breadcrumbs, PageHead } from "@/components/nav";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AuditList } from "@/components/ProductEditor";
import { ChangeList, StorePreview, VersionTrack, textChanges } from "@/components/Changes";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

const FIELDS = [["title", 120], ["subtitle", 200], ["description", 5000]] as const;

function AppManage() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useApp();
  const { data, error, loading, reload, setData } = useLoad<any>(`/moderation/catalog/apps/${id}`);
  const cats = useLoad<any>("/categories?kind=app");
  const [texts, setTexts] = useState<any>({ ru: {}, en: {} });
  const [category, setCategory] = useState("");
  const [reason, setReason] = useState("");
  const [flash, setFlash] = useState<string | null>(null);
  const act = useAction();
  useEffect(() => {
    if (!data) return;
    setTexts({ ru: { ...(data.texts.ru ?? {}) }, en: { ...(data.texts.en ?? {}) } });
    setCategory(data.category ?? "");
  }, [data]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const a = data;
  // NAP-116: what saving will change, word by word, and how the card will look after it
  const orig = Object.fromEntries(["ru", "en"].map((l) => [l, { title: a.texts[l]?.title ?? "", subtitle: a.texts[l]?.subtitle ?? "", description: a.texts[l]?.description ?? "" }]));
  const edits = [...textChanges(orig, texts), ...(category !== (a.category ?? "") ? [{ area: "category", kind: "changed" as const, before: a.category, after: category }] : [])];
  const textEdits = edits.filter((c) => c.area === "text");
  const catName = (slug: string | null) => (cats.data?.items ?? []).find((c: any) => c.slug === slug)?.name ?? slug ?? "—";
  const reset = () => { setTexts({ ru: { ...(a.texts.ru ?? {}) }, en: { ...(a.texts.en ?? {}) } }); setCategory(a.category ?? ""); };
  const done = (x: any, m: string) => { setData(x); setReason(""); setFlash(m); };
  const save = async () => {
    const body = { data: { category, texts: Object.fromEntries(["ru", "en"].filter((l) => texts[l]?.title).map((l) => [l, { title: texts[l].title, subtitle: texts[l].subtitle ?? "", description: texts[l].description ?? "" }])) }, reason };
    const x = await act.run(() => api<any>(`/moderation/catalog/apps/${id}`, { method: "PUT", body }));
    if (x) done(x, t("mm.done_saved"));
  };
  const action = async (path: string, msg: string) => {
    const x = await act.run(() => api<any>(`/moderation/catalog/apps/${id}/${path}`, { body: { reason } }));
    if (x) done(x, msg);
  };
  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { href: `/${locale}/moderation?tab=apps`, label: t("mod.title") }, { label: a.texts[locale]?.title ?? a.texts.ru?.title ?? a.slug }]} />
      <div className="spread">
        <div><h1>{a.texts[locale]?.title ?? a.texts.ru?.title ?? a.slug}</h1><div className="hint">{a.slug} · {a.author}{a.latest ? ` · v${a.latest.label}` : ""}</div></div>
        <div className="row">
          {a.deleted_at ? <span className="badge bad">{t("mm.deleted")}</span> : a.published ? <span className="badge ok">{t("mm.in_store")}</span> : <span className="badge gray">{t("mm.not_published")}</span>}
          {!a.deleted_at && a.sales_status === "withdrawn" && <span className="badge warn">{t("mm.sales_stopped")}</span>}
          {a.latest && <StatusBadge ns="version_state" value={a.latest.state} />}
        </div>
      </div>
      {a.published && !a.deleted_at && <Link href={`/${locale}/apps/${a.slug}`}>{t("mod.open_in_store")}</Link>}
      {a.deleted_at && <div className="notice bad">{t("mm.deleted_note", { date: date(a.deleted_at, locale, true), reason: a.deleted_reason ?? "—" })}{!a.restorable && ` ${t("mm.restore_expired")}`}</div>}
      <VersionTrack sameAsLive={!!a.live && a.latest?.id === a.live.id}
        live={a.live ? { label: a.live.label, at: a.live.published_at, note: t("chg.published") } : null}
        next={a.latest ? { label: a.latest.label, at: null, note: t(`version_state.${a.latest.state}`) } : null} />
      {flash && <div className="notice ok" role="status">{flash}</div>}
      <p className="hint">{t("mm.app_scope_hint")}</p>
      <fieldset className="stack" disabled={!!a.deleted_at} style={{ border: 0, padding: 0, margin: 0 }}>
        <label className="field" style={{ maxWidth: 320 }}>{t("fields.category")}<select value={category} onChange={(e) => setCategory(e.target.value)}>
          {(cats.data?.items ?? []).map((c: any) => <option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label>
        <div className="two">{(["ru", "en"] as const).map((l) => (
          <fieldset key={l} className="panel stack" lang={l}><legend>{t(`sp.lang_${l}`)}</legend>
            {texts[l]?.status && <span className="hint">{t("sp.text_status", { status: t(`tr_status.${texts[l].status}`) })}</span>}
            {FIELDS.map(([k, max]) => (
              <label key={k} className="field">{t(`mm.app_${k}`)}
                {k === "description"
                  ? <textarea rows={6} maxLength={max} value={texts[l]?.[k] ?? ""} onChange={(e) => setTexts({ ...texts, [l]: { ...texts[l], [k]: e.target.value } })} />
                  : <input maxLength={max} value={texts[l]?.[k] ?? ""} onChange={(e) => setTexts({ ...texts, [l]: { ...texts[l], [k]: e.target.value } })} />}
              </label>))}
          </fieldset>))}</div>
      </fieldset>
      <StorePreview texts={texts} slug={a.slug} hue={a.cover_hue} author={a.author} />
      <section className="panel stack" aria-labelledby="edits-h" data-testid="pending-edits">
        <h2 id="edits-h">{t("chg.pending_title")} {edits.length > 0 && <span className="hint">{edits.length}</span>}</h2>
        {(textEdits.length > 0 || !edits.length) && <ChangeList changes={textEdits} empty={t("chg.pending_none")} />}
        {edits.some((c) => c.area === "category") && <p><span className="chg-kind is-changed">{t("chg.kind.changed")}</span><strong>{t("fields.category")}</strong> <span className="hint">{catName(a.category)} → {catName(category)}</span></p>}
        {edits.length > 0 && <div className="row"><button type="button" className="btn ghost small" onClick={reset}>{t("chg.discard")}</button></div>}
      </section>
      <div className="panel stack">
        <label className="field">{t("mm.reason")}<textarea rows={2} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} /><span className="hint">{t("mm.reason_hint")}</span></label>
        <div className="row">
          {!a.deleted_at && <button className="btn" disabled={act.pending || !reason.trim() || !edits.length} onClick={save}>{edits.length ? t("mm.save_n_changes", { count: edits.length }) : t("mm.save_changes")}</button>}
          {!a.deleted_at && (a.sales_status === "active"
            ? <button className="btn ghost" disabled={act.pending || !reason.trim()} onClick={() => action("stop-sales", t("mm.done_sales_stopped"))}>{t("dev.stop_sales")}</button>
            : <button className="btn ghost" disabled={act.pending} onClick={() => action("resume-sales", t("mm.done_sales_resumed"))}>{t("dev.resume_sales")}</button>)}
          {!a.deleted_at && <button className="btn danger" disabled={act.pending || !reason.trim()} onClick={() => action("delete", t("mm.done_deleted"))}>{t("mm.delete")}</button>}
          {a.deleted_at && a.restorable && <button className="btn" disabled={act.pending} onClick={() => action("restore", t("mm.done_restored"))}>{t("mm.restore")}</button>}
        </div>
        <p className="hint">{t("mm.app_delete_hint")}</p>
        <ErrorBox error={act.error} />
      </div>
      <AuditList items={a.audit ?? []} />
    </div>
  );
}

export default function ModeratorAppPage() {
  return <div className="section"><RequireAuth role={["moderator", "admin"]}><AppManage /></RequireAuth></div>;
}
