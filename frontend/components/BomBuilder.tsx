"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { money } from "@/lib/i18n";
import { CompatBadge, availabilityText } from "./cards";
import { useApp } from "./Providers";
import { ErrorBox, Loading, useAction } from "./ui";

export function BomBuilder({ slug, app }: { slug: string; app?: string }) {
  const { t, locale, refreshCart } = useApp();
  const [owned, setOwned] = useState<string[]>([]);
  const [optional, setOptional] = useState<string[]>([]);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [bom, setBom] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [added, setAdded] = useState<any>(null);
  const act = useAction();

  const load = useCallback(async () => {
    setErr(null);
    try {
      const qs = new URLSearchParams({ owned: owned.join(",") });
      if (app) qs.set("app", app);
      setBom(await api(`/hardware/kits/${slug}/bom?${qs}`));
    } catch (e) { setErr(e); }
  }, [slug, app, owned]);
  useEffect(() => { load(); }, [load]);

  if (err) return <ErrorBox error={err} onRetry={load} />;
  if (!bom) return <Loading />;
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <section className="panel stack" aria-labelledby="bom-h">
      <h2 id="bom-h">{t("kit.bom_title", { v: bom.bom_version })}</h2>
      {bom.app && (
        bom.unmet_capabilities.length ? <div className="notice warn">{t("kit.unmet")}: {bom.unmet_capabilities.map((c: string) => t(`caps.${c}`)).join(", ")}</div>
          : <div className="notice">{t("kit.covers_app")}{bom.binaural_path_verified ? "" : " " + t("kit.binaural_unverified")}</div>
      )}
      <div className="scroll-x">
        <table>
          <thead><tr><th>{t("kit.role")}</th><th>{t("kit.item")}</th><th>{t("kit.status")}</th><th>{t("kit.compat")}</th><th>{t("kit.i_have")}</th></tr></thead>
          <tbody>
            {bom.items.map((it: any) => (
              <tr key={it.item_id}>
                <td>{t(`roles_hw.${it.role}`)}{it.required ? "" : ` (${t("kit.optional")})`}</td>
                <td>
                  <div>{it.sku.title} × {it.quantity}</div>
                  <div className="hint">{money(it.sku.price_minor, it.sku.currency, locale)} · {it.sku.seller.name} · {availabilityText(it.sku.availability, t)}</div>
                  {it.alternatives.length > 0 && it.status !== "owned" && (
                    <label className="field hint">{t("kit.alternative")}
                      <select value={choices[it.item_id] ?? it.sku.id} onChange={(e) => setChoices({ ...choices, [it.item_id]: e.target.value })}>
                        <option value={it.sku.id}>{it.sku.code}</option>
                        {it.alternatives.map((a: any) => <option key={a.id} value={a.id}>{a.code} — {money(a.price_minor, a.currency, locale)} ({t(`compat.${a.compatibility[0]?.status ?? "unverified"}`)})</option>)}
                      </select></label>
                  )}
                  {!it.required && it.status !== "owned" && (
                    <label className="check hint"><input type="checkbox" checked={optional.includes(it.item_id)} onChange={() => setOptional(toggle(optional, it.item_id))} /> {t("kit.include_optional")}</label>
                  )}
                </td>
                <td><span className={`badge ${it.status === "owned" ? "ok" : it.status === "missing" ? "warn" : "gray"}`}>{t(`kit.st_${it.status}`)}</span></td>
                <td>{it.sku.compatibility.map((c: any, i: number) => <div key={i}>{t(`feature.${c.feature}`)}: <CompatBadge status={c.status} t={t} /></div>)}</td>
                <td><input type="checkbox" aria-label={t("kit.i_have")} checked={owned.includes(it.sku.id) || (it.owned_sku_id && owned.includes(it.owned_sku_id))}
                           onChange={() => setOwned(toggle(owned, it.owned_sku_id ?? it.sku.id))} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {bom.missing_groups.length > 0 && (
        <div className="stack">
          {bom.missing_groups.map((g: any) => (
            <div key={g.seller.id + g.currency}>{t("kit.group", { seller: g.seller.name, count: g.items })}: <strong>{money(g.total_minor, g.currency, locale)}</strong></div>
          ))}
          {bom.separate_orders && <div className="notice warn">{t("kit.separate_orders")}</div>}
        </div>
      )}
      {bom.note_codes.includes("digital_license_not_included") && <p className="hint">{t("kits.license_note")}</p>}
      <div className="row">
        <button className="btn" disabled={act.pending} onClick={async () => {
          const r = await act.run(() => api("/hardware/cart/bom", { body: { product_slug: slug, owned_sku_ids: owned, choices, include_optional: optional } }));
          if (r) { setAdded(r); refreshCart(); }
        }}>{t("kit.add_missing")}</button>
        {added && <Link href={`/${locale}/cart`}>{t("kit.added", { count: added.added })}</Link>}
      </div>
      <ErrorBox error={act.error} />
    </section>
  );
}
