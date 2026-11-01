"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "./Providers";
import { SavedAddressList, useSavedAddresses } from "./SavedAddresses";
import { ErrorBox, useAction, useDraft } from "./ui";

const COUNTRIES = ["AM", "AE", "AT", "BE", "CA", "CH", "CN", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GE", "GR",
  "HK", "IE", "IL", "IT", "JP", "KR", "KZ", "LT", "LV", "NL", "NO", "NZ", "PL", "PT", "RS", "RU", "SE", "SG", "TR", "US"];

export function AddressForm({ orderId, initial, onSaved }: { orderId: string; initial: any; onSaved: (o: any) => void }) {
  const { t, locale } = useApp();
  const [country, setCountry] = useState<string>(initial?.country ?? "DE");
  const [fields, setFields, clearDraft] = useDraft<Record<string, string>>(`addr:${orderId}`, initial?.fields ?? {});
  const [rules, setRules] = useState<any>(null);
  const act = useAction();
  const saved = useSavedAddresses();
  const book = saved.data?.items ?? [];
  const pick = async (id: string) => {  // NAP-023: one action; the server checks this order's rules again
    const r = await act.run(() => api(`/hardware/orders/${orderId}/address`, { method: "PUT", body: { address_id: id } }));
    if (r) { clearDraft(); onSaved(r); return; }
    const a = book.find((x) => x.id === id);  // did not pass this order's rules: open it in the form, fields highlighted
    if (a) { setCountry(a.country); setFields({ ...a.fields }); }
  };
  useEffect(() => { api(`/hardware/address-rules/${country}`).then(setRules).catch(() => setRules(null)); }, [country]);
  const dn = new Intl.DisplayNames([locale], { type: "region" });
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setFields({ ...fields, [k]: e.target.value });
  const bad: string[] = ((act.error as any)?.details?.fields as string[]) ?? [];
  const F = (k: string, required: boolean, auto?: string) => (
    <label className="field" key={k}>{t(`fields.${k}`)}{required ? " *" : ""}
      <input value={fields[k] ?? ""} onChange={set(k)} autoComplete={auto} aria-invalid={bad.includes(k)} required={required} />
      {bad.includes(k) && <span className="err-text">{t("address.invalid_field")}</span>}
    </label>
  );
  return (
    <div className="stack">
    {book.length > 0 && (
      <section className="stack" aria-labelledby="saved-addr-h">
        <h3 id="saved-addr-h">{t("address.saved_title")}</h3>
        <SavedAddressList items={book} action={t("address.use")} onAction={pick} pending={act.pending} />
        <p className="hint">{t("address.recheck_hint")} {t("address.or_new")}</p>
      </section>
    )}
    <form className="stack" onSubmit={async (e) => {
      e.preventDefault();
      const r = await act.run(() => api(`/hardware/orders/${orderId}/address`, { method: "PUT", body: { country, fields } }));
      if (r) { clearDraft(); onSaved(r); }
    }}>
      <div className="form wide">
        <label className="field">{t("fields.country")} *
          <select value={country} onChange={(e) => setCountry(e.target.value)}>
            {COUNTRIES.map((c) => <option key={c} value={c}>{dn.of(c)}</option>)}
          </select></label>
        {F("name", true, "name")}
        {F("company", false, "organization")}
        {F("line1", true, "address-line1")}
        {F("line2", false, "address-line2")}
        {F("city", true, "address-level2")}
        {rules && (rules.region_required || country === "US") ? F("region", rules.region_required, "address-level1") : F("region", false, "address-level1")}
        {rules?.postal_supported !== false && F("postal_code", !!rules?.postal_required, "postal-code")}
        {F("phone", true, "tel")}
      </div>
      {rules?.latin_required && (
        <div className="stack">
          <p className="hint">{t("address.latin_hint")}</p>
          <div className="form wide">{F("name_latin", false)}{F("line1_latin", false)}{F("city_latin", false)}</div>
        </div>
      )}
      <ErrorBox error={act.error} />
      <div><button className="btn" disabled={act.pending}>{t("address.save")}</button></div>
    </form>
    </div>
  );
}
