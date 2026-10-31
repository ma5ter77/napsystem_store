"use client";

// NAP-084: site content editor (role content_editor) — UI texts, e-mail templates and categories.
// Draft -> publish; every publication is a version you can return to; "built-in" restores the original text.
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, RequireAuth, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date, flatDict } from "@/lib/i18n";

const TABS = ["texts", "mail", "categories"] as const;
type Row = { key: string; ru?: string; en?: string };

function TextEditor({ k, lang, builtin, over, reload }: { k: string; lang: "ru" | "en"; builtin: string; over?: any; reload: () => void }) {
  const { t, locale } = useApp();
  const act = useAction();
  const [value, setValue] = useState<string>(over?.draft ?? over?.published ?? builtin ?? "");
  const [hist, setHist] = useState<any[] | null>(null);
  const call = async (path: string, init: any, refresh = true) => {
    if (await act.run(() => api(`/content-editor/texts/${encodeURIComponent(k)}/${lang}${path}`, init))) { if (refresh) reload(); return true; }
    return false;
  };
  const showHist = async () => setHist((await api<any>(`/content-editor/texts/${encodeURIComponent(k)}/${lang}/history`)).items);
  return (
    <div className="panel flat stack" lang={lang}>
      <div className="spread"><strong>{t(`sp.lang_${lang}`)}</strong>
        <span className="row">{over?.published != null && <span className="badge ok">{t("ce.overridden")}</span>}{over?.draft != null && <span className="badge warn">{t("ce.has_draft")}</span>}</span></div>
      <div className="hint">{t("ce.builtin")}: {builtin || "—"}</div>
      <textarea rows={Math.min(8, Math.max(2, Math.ceil((value || "").length / 70)))} value={value} onChange={(e) => setValue(e.target.value)} maxLength={5000} />
      {/\{\w+/.test(builtin) && <span className="hint">{t("ce.params_hint")}</span>}
      <div className="row">
        <button className="btn small" disabled={act.pending || !value.trim()} onClick={() => call("", { method: "PUT", body: { value } })}>{t("ce.save_draft")}</button>
        {over?.draft != null && <button className="btn small" disabled={act.pending} onClick={() => call("/publish", { method: "POST" })}>{t("ce.publish")}</button>}
        {over?.draft != null && <button className="btn ghost small" disabled={act.pending} onClick={async () => { if (await call("/discard", { method: "POST" })) setValue(over?.published ?? builtin); }}>{t("ce.discard")}</button>}
        {over?.published != null && <button className="btn ghost small" disabled={act.pending} onClick={async () => { if (await call("/reset", { method: "POST" })) setValue(builtin); }}>{t("ce.reset")}</button>}
        <button className="btn ghost small" onClick={showHist}>{t("ce.history")}</button>
      </div>
      {hist && (hist.length === 0 ? <span className="hint">{t("ce.no_history")}</span> : (
        <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>{hist.map((h) => (
          <li key={h.id} className="row"><span className="hint">{date(h.at, locale, true)} · {h.by ?? "—"} · {t(`ce.note.${h.note ?? "publish"}`)}</span>
            <span style={{ flex: 1 }}>{h.value ?? <em>{t("ce.builtin_short")}</em>}</span>
            <button className="btn ghost small" disabled={act.pending} onClick={async () => { if (await call("/revert", { method: "POST", body: { version_id: h.id } })) { setValue(h.value ?? builtin); setHist(null); } }}>{t("ce.revert")}</button></li>))}</ul>))}
      <ErrorBox error={act.error} />
    </div>
  );
}

function TextsTab({ mail }: { mail: boolean }) {
  const { t } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/content-editor/texts");
  const tpl = useLoad<any>(mail ? "/content-editor/mail-templates" : null);
  const [q, setQ] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const rows: Row[] = useMemo(() => {
    if (mail) return (tpl.data?.items ?? []) as Row[];
    const ru = flatDict("ru"), en = flatDict("en");
    return Object.keys(ru).map((key) => ({ key, ru: ru[key], en: en[key] }));
  }, [mail, tpl.data]);
  const over = useMemo(() => {
    const m: Record<string, Record<string, any>> = {};
    for (const r of data?.items ?? []) (m[r.key] ??= {})[r.locale] = r;
    return m;
  }, [data]);
  if ((loading && !data) || (mail && tpl.loading && !tpl.data)) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const ql = q.trim().toLowerCase();
  const shown = rows.filter((r) => (!onlyChanged || over[r.key]) && (!ql || r.key.toLowerCase().includes(ql) || (r.ru ?? "").toLowerCase().includes(ql) || (r.en ?? "").toLowerCase().includes(ql))).slice(0, 60);
  return (
    <div className="stack">
      <p className="hint">{mail ? t("ce.mail_hint") : t("ce.texts_hint")}</p>
      <div className="row">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("ce.search")} aria-label={t("ce.search")} style={{ minWidth: 280 }} />
        <label className="check"><input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> {t("ce.only_changed")}</label>
        <span className="hint">{t("ce.found", { count: shown.length })}</span>
      </div>
      {!shown.length ? <Empty>{t("mm.nothing_found")}</Empty> : shown.map((r) => (
        <div key={r.key} className="panel stack">
          <button type="button" className="linklike" style={{ textAlign: "left" }} aria-expanded={open === r.key} onClick={() => setOpen(open === r.key ? null : r.key)}>
            <code>{r.key}</code> {over[r.key] && <span className="badge ok">{t("ce.changed")}</span>}
          </button>
          {open !== r.key ? <div className="hint">{(over[r.key]?.ru?.published ?? r.ru ?? "").slice(0, 160)}</div> : (
            <div className="two">{(["ru", "en"] as const).map((l) => <TextEditor key={l + (over[r.key]?.[l]?.updated_at ?? "")} k={r.key} lang={l} builtin={r[l] ?? ""} over={over[r.key]?.[l]} reload={reload} />)}</div>)}
        </div>))}
    </div>
  );
}

function CategoriesTab() {
  const { t } = useApp();
  const [kind, setKind] = useState("app");
  const { data, error, loading, reload } = useLoad<any>(`/content-editor/categories?kind=${kind}`, [kind]);
  const [edit, setEdit] = useState<Record<string, any>>({});
  const [nw, setNw] = useState({ slug: "", ru: "", en: "" });
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const val = (c: any) => edit[c.id] ?? { ru: c.names.ru ?? "", en: c.names.en ?? "", position: c.position };
  return (
    <div className="stack">
      <div className="tabs" role="tablist">{["app", "hardware"].map((k) => <button key={k} role="tab" aria-selected={kind === k} onClick={() => setKind(k)}>{t(`ce.kind_${k}`)}</button>)}</div>
      <div className="scroll-x"><table>
        <thead><tr><th>{t("fields.slug")}</th><th>RU</th><th>EN</th><th>{t("ce.position")}</th><th>{t("ce.used")}</th><th /></tr></thead>
        <tbody>{data.items.map((c: any) => { const v = val(c); return (
          <tr key={c.id}>
            <td><code>{c.slug}</code>{c.missing.length > 0 && <div><span className="badge warn">{t("ce.missing", { langs: c.missing.join(", ").toUpperCase() })}</span></div>}</td>
            <td><input value={v.ru} aria-label="RU" onChange={(e) => setEdit({ ...edit, [c.id]: { ...v, ru: e.target.value } })} /></td>
            <td><input value={v.en} aria-label="EN" onChange={(e) => setEdit({ ...edit, [c.id]: { ...v, en: e.target.value } })} /></td>
            <td><input type="number" style={{ width: 70 }} value={v.position} aria-label={t("ce.position")} onChange={(e) => setEdit({ ...edit, [c.id]: { ...v, position: Number(e.target.value) } })} /></td>
            <td>{c.used}</td>
            <td className="row">
              <button className="btn small" disabled={act.pending || !edit[c.id]} onClick={async () => { if (await act.run(() => api(`/content-editor/categories/${c.id}`, { method: "PUT", body: { names: { ru: v.ru, en: v.en }, position: v.position } }))) { const e2 = { ...edit }; delete e2[c.id]; setEdit(e2); reload(); } }}>{t("common.save")}</button>
              {c.used === 0 && <button className="btn danger small" disabled={act.pending} onClick={async () => { if (await act.run(() => api(`/content-editor/categories/${c.id}`, { method: "DELETE" }))) reload(); }}>{t("mm.delete")}</button>}
            </td>
          </tr>); })}</tbody>
      </table></div>
      <div className="panel stack">
        <h3>{t("ce.new_category")}</h3>
        <div className="grid3">
          <label className="field">{t("fields.slug")}<input value={nw.slug} onChange={(e) => setNw({ ...nw, slug: e.target.value.toLowerCase() })} placeholder="breathing" /></label>
          <label className="field">RU<input value={nw.ru} onChange={(e) => setNw({ ...nw, ru: e.target.value })} /></label>
          <label className="field">EN<input value={nw.en} onChange={(e) => setNw({ ...nw, en: e.target.value })} /></label>
        </div>
        <div><button className="btn" disabled={act.pending || !nw.slug || !(nw.ru || nw.en)} onClick={async () => { if (await act.run(() => api("/content-editor/categories", { body: { kind, slug: nw.slug, names: { ru: nw.ru, en: nw.en } } }))) { setNw({ slug: "", ru: "", en: "" }); reload(); } }}>{t("ce.add")}</button></div>
      </div>
      <ErrorBox error={act.error} />
    </div>
  );
}

function ContentEditor() {
  const { t, locale } = useApp();
  const router = useRouter();
  const tab = (useSearchParams()?.get("tab") as (typeof TABS)[number]) || "texts";
  return (
    <div className="stack">
      <h1>{t("ce.title")}</h1>
      <div className="tabs" role="tablist">{TABS.map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/${locale}/content?tab=${x}`)}>{t(`ce.tab_${x}`)}</button>)}</div>
      {tab === "texts" && <TextsTab key="t" mail={false} />}
      {tab === "mail" && <TextsTab key="m" mail />}
      {tab === "categories" && <CategoriesTab />}
    </div>
  );
}

export default function ContentPage() {
  return <div className="section"><RequireAuth role={["content_editor", "admin"]}><Suspense><ContentEditor /></Suspense></RequireAuth></div>;
}
