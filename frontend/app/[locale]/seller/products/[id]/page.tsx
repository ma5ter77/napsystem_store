"use client";

// NAP-022: supplier product editor — basics, variants (SKU), RU/EN texts with box contents, moderation.
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, Price, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

const TABS = ["basics", "skus", "texts", "submit"] as const;
const CURRENCIES = ["EUR", "USD", "RSD", "GBP"];
const BATTERY = ["none", "li_ion_contained", "li_ion_packed", "li_metal"];

const pairsToText = (o: Record<string, unknown> | null | undefined) => Object.entries(o ?? {}).map(([k, v]) => `${k} = ${v}`).join("\n");
const textToPairs = (s: string) => Object.fromEntries(s.split("\n").map((l) => l.split("=")).filter((p) => p[0]?.trim()).map(([k, ...v]) => [k.trim(), v.join("=").trim()]));
const toMinor = (s: string) => Math.round(Number(String(s).replace(",", ".")) * 100);

type Done = (m: string) => void;

function Basics({ p, data, onSaved }: { p: any; data: any; onSaved: (x: any, m: string) => void }) {
  const { t } = useApp();
  const cats = useLoad<any>("/categories?kind=hardware");
  const [f, setF] = useState({
    category: p.category, brand: p.brand, model: p.model, warehouse_id: p.warehouse_id, warranty_months: p.warranty_months,
    made_to_order: p.made_to_order, lead_time_days: p.lead_time_days, prep_days: p.prep_days, assembly_included: p.assembly_included,
    freight_mode: p.freight_mode, destinations: p.destinations.join(", "), specs: pairsToText(p.specs),
  });
  const act = useAction();
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });
  const save = async () => {
    const body = { ...f, destinations: f.destinations.split(/[\s,;]+/).filter(Boolean), specs: textToPairs(f.specs) };
    const x = await act.run(() => api<any>(`/seller/products/${p.id}`, { method: "PUT", body }));
    if (x) onSaved(x, t("sp.saved"));
  };
  return (
    <fieldset className="panel stack" disabled={!p.editable}>
      <div className="grid3">
        <label className="field">{t("fields.category")}<select value={f.category} onChange={(e) => set("category", e.target.value)}>
          {(cats.data?.items ?? [{ slug: p.category, name: p.category_name }]).map((c: any) => <option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label>
        <label className="field">{t("fields.brand")}<input value={f.brand} maxLength={80} onChange={(e) => set("brand", e.target.value)} /></label>
        <label className="field">{t("fields.model")}<input value={f.model} maxLength={80} onChange={(e) => set("model", e.target.value)} /></label>
        <label className="field">{t("sp.warehouse")}<select value={f.warehouse_id} onChange={(e) => set("warehouse_id", e.target.value)}>
          {(data?.warehouses ?? []).map((w: any) => <option key={w.id} value={w.id}>{w.name} ({w.country})</option>)}</select></label>
        <label className="field">{t("sp.warranty")}<input type="number" min={0} max={120} value={f.warranty_months} onChange={(e) => set("warranty_months", Number(e.target.value))} /></label>
        <label className="field">{t("sp.prep_days")}<input type="number" min={0} max={60} value={f.prep_days} onChange={(e) => set("prep_days", Number(e.target.value))} /></label>
      </div>
      <div className="row">
        <label className="check"><input type="checkbox" checked={f.made_to_order} onChange={(e) => set("made_to_order", e.target.checked)} /> {t("sp.made_to_order")}</label>
        {f.made_to_order && <label className="field">{t("sp.lead_time")}<input type="number" min={0} max={365} style={{ width: 100 }} value={f.lead_time_days} onChange={(e) => set("lead_time_days", Number(e.target.value))} /></label>}
        <label className="check"><input type="checkbox" checked={f.assembly_included} onChange={(e) => set("assembly_included", e.target.checked)} /> {t("sp.assembly")}</label>
      </div>
      <div className="grid3">
        <label className="field">{t("sp.freight")}<select value={f.freight_mode} onChange={(e) => set("freight_mode", e.target.value)}>
          {["parcel", "custom_quote"].map((x) => <option key={x} value={x}>{t(`sp.freight_${x}`)}</option>)}</select></label>
        <label className="field">{t("sp.destinations")}<input value={f.destinations} onChange={(e) => set("destinations", e.target.value.toUpperCase())} placeholder="DE, RS, AM" /><span className="hint">{t("sp.destinations_hint")}</span></label>
      </div>
      <label className="field">{t("sp.specs")}<textarea rows={5} value={f.specs} onChange={(e) => set("specs", e.target.value)} placeholder="impedance_ohm = 32" /><span className="hint">{t("sp.specs_hint")}</span></label>
      <ErrorBox error={act.error} />
      {p.editable && <div><button type="button" className="btn" disabled={act.pending} onClick={save}>{t("common.save")}</button></div>}
    </fieldset>
  );
}

function SkuForm({ p, sku, onSaved }: { p: any; sku?: any; onSaved: (x: any, m: string) => void }) {
  const { t } = useApp();
  const live = !p.editable; // published: only price and on/off
  const [f, setF] = useState({
    code: sku?.code ?? "", variant: pairsToText(sku?.variant), price: sku ? (sku.price_minor / 100).toFixed(2) : "",
    currency: sku?.currency ?? "EUR", weight_g: sku?.weight_g || "", length_mm: sku?.length_mm || "", width_mm: sku?.width_mm || "",
    height_mm: sku?.height_mm || "", package_count: sku?.package_count ?? 1, origin_country: sku?.origin_country ?? "",
    hs_code: sku?.hs_code ?? "", customs_description: sku?.customs_description ?? "", contains_battery: sku?.contains_battery ?? "none",
    active: sku?.active ?? true,
  });
  const act = useAction();
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });
  const save = async () => {
    const num = (v: unknown) => Number(v) || 0;
    const body: any = live ? { price_minor: toMinor(f.price), active: f.active } : {
      variant: textToPairs(f.variant), price_minor: toMinor(f.price), currency: f.currency, weight_g: num(f.weight_g),
      length_mm: num(f.length_mm), width_mm: num(f.width_mm), height_mm: num(f.height_mm), package_count: num(f.package_count),
      origin_country: f.origin_country, hs_code: f.hs_code, customs_description: f.customs_description,
      contains_battery: f.contains_battery, active: f.active, ...(sku ? {} : { code: f.code }),
    };
    const x = await act.run(() => sku
      ? api<any>(`/seller/products/${p.id}/skus/${sku.id}`, { method: "PUT", body })
      : api<any>(`/seller/products/${p.id}/skus`, { body }));
    if (x) onSaved(x, sku ? t("sp.saved") : t("sp.sku_added"));
  };
  return (
    <div className={`panel stack sku-card${f.active ? "" : " off"}`}>
      {sku && <div className="spread"><strong>{sku.code}</strong><span className="hint">{t("seller.on_hand")}: {sku.on_hand}</span></div>}
      <div className="grid3">
        {!sku && <label className="field">{t("sp.code")}<input value={f.code} onChange={(e) => set("code", e.target.value.toUpperCase())} /><span className="hint">{t("sp.code_hint")}</span></label>}
        <label className="field">{t("sp.price")}<input inputMode="decimal" value={f.price} onChange={(e) => set("price", e.target.value)} /></label>
        <label className="field">{t("sp.currency")}<select value={f.currency} disabled={live} onChange={(e) => set("currency", e.target.value)}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</select></label>
      </div>
      <fieldset className="stack" disabled={live} style={{ border: 0, padding: 0, margin: 0 }}>
        <div className="grid3">
          <label className="field">{t("sp.variant")}<textarea rows={2} value={f.variant} onChange={(e) => set("variant", e.target.value)} placeholder={t("sp.variant_hint")} /></label>
          <label className="field">{t("sp.weight")}<input type="number" min={1} value={f.weight_g} onChange={(e) => set("weight_g", e.target.value)} /></label>
          <div className="field"><span>{t("sp.dims")}</span><div className="dims">
            {(["length_mm", "width_mm", "height_mm"] as const).map((k) => <input key={k} type="number" min={1} aria-label={t(`fields.${k}`)} value={f[k]} onChange={(e) => set(k, e.target.value)} />)}</div></div>
          <label className="field">{t("sp.packages")}<input type="number" min={1} max={20} value={f.package_count} onChange={(e) => set("package_count", e.target.value)} /></label>
          <label className="field">{t("sp.origin")}<input maxLength={2} value={f.origin_country} onChange={(e) => set("origin_country", e.target.value.toUpperCase())} placeholder="DE" /></label>
          <label className="field">{t("sp.battery")}<select value={f.contains_battery} onChange={(e) => set("contains_battery", e.target.value)}>{BATTERY.map((b) => <option key={b} value={b}>{t(`battery.${b}`)}</option>)}</select></label>
          <label className="field">{t("sp.hs")}<input value={f.hs_code} onChange={(e) => set("hs_code", e.target.value)} /></label>
          <label className="field">{t("sp.customs")}<input value={f.customs_description} maxLength={255} onChange={(e) => set("customs_description", e.target.value)} /></label>
        </div>
      </fieldset>
      <div className="row">
        <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => set("active", e.target.checked)} /> {t("sp.active")}</label>
        <button type="button" className="btn small" disabled={act.pending || !f.price} onClick={save}>{sku ? t("common.save") : t("sp.add_sku")}</button>
        {sku && f.price && <span className="hint"><Price minor={toMinor(f.price)} currency={f.currency} /></span>}
      </div>
      <ErrorBox error={act.error} />
    </div>
  );
}

function Skus({ p, onSaved }: { p: any; onSaved: (x: any, m: string) => void }) {
  const { t } = useApp();
  const [adding, setAdding] = useState(p.skus.length === 0);
  return (
    <div className="stack">
      {!p.editable && <div className="notice">{t("sp.live_hint")}</div>}
      {p.skus.length === 0 && <p className="hint">{t("sp.no_skus")}</p>}
      {p.skus.map((s: any) => <SkuForm key={s.id + p.status} p={p} sku={s} onSaved={onSaved} />)}
      {p.editable && (adding
        ? <SkuForm key={`new-${p.skus.length}`} p={p} onSaved={(x, m) => { setAdding(false); onSaved(x, m); }} />
        : <div><button type="button" className="btn ghost" onClick={() => setAdding(true)}>{t("sp.add_sku")}</button></div>)}
      <p className="hint">{t("sp.stock_hint")}</p>
    </div>
  );
}

function TextsForm({ p, lang, onSaved }: { p: any; lang: "ru" | "en"; onSaved: (x: any, m: string) => void }) {
  const { t } = useApp();
  const cur = p.texts[lang] ?? {};
  const [f, setF] = useState({ title: cur.title ?? "", summary: cur.summary ?? "", description: cur.description ?? "",
    in_box: (cur.in_box ?? []).join("\n"), return_terms: cur.return_terms ?? "", delivery_terms: cur.delivery_terms ?? "" });
  const act = useAction();
  const set = (k: string, v: string) => setF({ ...f, [k]: v });
  const save = async () => {
    const x = await act.run(() => api<any>(`/seller/products/${p.id}/texts/${lang}`, { method: "PUT", body: { ...f, in_box: f.in_box.split("\n") } }));
    if (x) onSaved(x, t("sp.saved"));
  };
  return (
    <fieldset className="panel stack" disabled={!p.editable} lang={lang}>
      <legend>{t(`sp.lang_${lang}`)}</legend>
      {cur.status && <span className="hint">{t("sp.text_status", { status: t(`tr_status.${cur.status}`) })}</span>}
      <label className="field">{t("sp.title")}<input value={f.title} maxLength={120} onChange={(e) => set("title", e.target.value)} /></label>
      <label className="field">{t("sp.summary")}<input value={f.summary} maxLength={300} onChange={(e) => set("summary", e.target.value)} /></label>
      <label className="field">{t("sp.description")}<textarea rows={6} value={f.description} maxLength={5000} onChange={(e) => set("description", e.target.value)} /></label>
      <label className="field">{t("sp.in_box")}<textarea rows={4} value={f.in_box} onChange={(e) => set("in_box", e.target.value)} /><span className="hint">{t("sp.in_box_hint")}</span></label>
      <label className="field">{t("sp.return_terms")}<textarea rows={2} value={f.return_terms} onChange={(e) => set("return_terms", e.target.value)} /></label>
      <label className="field">{t("sp.delivery_terms")}<textarea rows={2} value={f.delivery_terms} onChange={(e) => set("delivery_terms", e.target.value)} /></label>
      <ErrorBox error={act.error} />
      {p.editable && <div><button type="button" className="btn" disabled={act.pending} onClick={save}>{t("common.save")}</button></div>}
    </fieldset>
  );
}

function Checklist({ p, goTab }: { p: any; goTab: (x: string) => void }) {
  const { t } = useApp();
  return (
    <div className="panel flat stack">
      <h3>{t("sp.check_title")}</h3>
      <ul className="checklist">{p.checks.map((c: any) => (
        <li key={c.key} className={c.ok ? "ok" : c.optional ? "warn" : "bad"}>
          <span aria-hidden="true">{c.ok ? "✓" : c.optional ? "!" : "✗"}</span>
          <span>{t(`sp.check.${c.key}`)}{!c.ok && c.optional ? ` — ${t("sp.optional")}` : ""}</span>
          {!c.ok && p.editable && <button type="button" className="btn ghost small" onClick={() => goTab(c.tab)}>{t("dev.fix")}</button>}
        </li>))}</ul>
    </div>
  );
}

function Submit({ p, onSaved, goTab }: { p: any; onSaved: (x: any, m: string) => void; goTab: (x: string) => void }) {
  const { t, locale } = useApp();
  const act = useAction();
  const run = async (path: string, msg: string) => {
    const x = await act.run(() => api<any>(`/seller/products/${p.id}/${path}`, { method: "POST" }));
    if (x) onSaved(x, msg);
  };
  return (
    <div className="stack">
      {p.editable && <Checklist p={p} goTab={goTab} />}
      <div className="panel stack">
        {p.editable && <><p className="hint">{t("sp.submit_hint")}</p>
          <div><button type="button" className="btn" disabled={act.pending || !p.ready} onClick={() => run("submit", t("sp.done_submitted"))}>{t("sp.submit")}</button></div></>}
        {p.status === "submitted" && <div><button type="button" className="btn ghost" disabled={act.pending} onClick={() => run("withdraw", t("sp.done_withdrawn"))}>{t("sp.withdraw")}</button></div>}
        {p.status === "published" && <><div className="row"><Link className="btn ghost" href={`/${locale}/devices/${p.slug}`}>{t("sp.open_in_catalog")}</Link>
          <button type="button" className="btn ghost" disabled={act.pending} onClick={() => run("withdraw", t("sp.done_withdrawn"))}>{t("sp.withdraw_live")}</button></div>
          <p className="hint">{t("sp.withdraw_live_hint")}</p></>}
        <ErrorBox error={act.error} />
      </div>
      {p.history.length > 0 && (
        <div className="panel flat stack">
          <h3>{t("sp.history")}</h3>
          {p.history.map((h: any, i: number) => (
            <div key={i}><StatusBadge ns="product_status" value={h.action === "withdrawn" ? "draft" : h.action} /> {t(`sp.hist.${h.action}`)} <span className="hint">{date(h.at, locale, true)}</span>{h.comment && <div>«{h.comment}»</div>}</div>))}
        </div>
      )}
    </div>
  );
}

function NextStep({ p, goTab }: { p: any; goTab: (x: string) => void }) {
  const { t, locale } = useApp();
  const missing = p.checks.filter((c: any) => !c.ok && !c.optional);
  const last = p.history[0];
  let cls = "notice", body: React.ReactNode = null;
  if (p.editable && missing.length) body = <>{p.status === "rejected" && <>{t("sp.next_rejected")}{last?.comment ? <> «{last.comment}»</> : null}. </>}{t("sp.next_fill")} {missing.map((c: any, i: number) => <span key={c.key}>{i ? ", " : ""}<button type="button" className="linklike" onClick={() => goTab(c.tab)}>{t(`sp.todo.${c.key}`)}</button></span>)}</>;
  else if (p.status === "rejected") { cls = "notice bad"; body = <>{t("sp.next_rejected")}{last?.comment ? <> «{last.comment}»</> : null}. {t("sp.next_rejected_fix")} <button type="button" className="btn small" onClick={() => goTab("submit")}>{t("sp.go_submit")}</button></>; }
  else if (p.editable) body = <>{t("sp.next_submit")} <button type="button" className="btn small" onClick={() => goTab("submit")}>{t("sp.go_submit")}</button></>;
  else if (p.status === "submitted") { cls = "notice warn"; body = t("sp.next_wait"); }
  else if (p.status === "published") { cls = "notice ok"; body = <>{t("sp.next_published")} <Link href={`/${locale}/devices/${p.slug}`}>{t("sp.open_in_catalog")}</Link></>; }
  if (!body) return null;
  return <div className={cls} role="status"><strong>{t("dev.next_step")}:</strong> {body}</div>;
}

function Editor() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useApp();
  const router = useRouter();
  const tab = (useSearchParams()?.get("tab") as (typeof TABS)[number]) || "basics";
  const { data: p0, error, loading, reload } = useLoad<any>(`/seller/products/${id}`);
  const list = useLoad<any>("/seller/products");
  const [p, setP] = useState<any>(null);
  const [flash, setFlash] = useState<string | null>(null);
  useEffect(() => { if (p0) setP(p0); }, [p0]);
  useEffect(() => { if (!flash) return; const x = setTimeout(() => setFlash(null), 6000); return () => clearTimeout(x); }, [flash]);
  if (loading && !p) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!p) return <Loading />;
  const goTab = (x: string) => router.replace(`/${locale}/seller/products/${id}?tab=${x}`);
  const onSaved = (x: any, m: string) => { setP(x); setFlash(m); };
  const title = p.texts[locale]?.title ?? p.texts.ru?.title ?? p.texts.en?.title ?? `${p.brand} ${p.model}`;
  return (
    <div className="stack">
      <div className="spread">
        <div><Link href={`/${locale}/seller?tab=products`}>{t("sp.back")}</Link><h1>{title}</h1><div className="hint">{p.slug}</div></div>
        <StatusBadge ns="product_status" value={p.status} />
      </div>
      <NextStep p={p} goTab={goTab} />
      <div className="tabs" role="tablist">{TABS.map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => goTab(x)}>{t(`sp.tab_${x}`)}</button>)}</div>
      {flash && <div className="notice ok" role="status">{flash}</div>}
      {tab === "basics" && <Basics key={p.status} p={p} data={list.data} onSaved={onSaved} />}
      {tab === "skus" && <Skus p={p} onSaved={onSaved} />}
      {tab === "texts" && <div className="stack"><p className="hint">{t("sp.texts_hint")}</p>
        <div className="two">{(["ru", "en"] as const).map((l) => <TextsForm key={l + p.status} p={p} lang={l} onSaved={onSaved} />)}</div></div>}
      {tab === "submit" && <Submit p={p} onSaved={onSaved} goTab={goTab} />}
    </div>
  );
}

export default function ProductEditorPage() {
  return <div className="section"><RequireAuth role="seller"><Suspense><Editor /></Suspense></RequireAuth></div>;
}
