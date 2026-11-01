"use client";

import { api } from "@/lib/api";
import { useApp } from "./Providers";
import { useLoad } from "./ui";

/** NAP-023: one line per saved address ("name, line1, city, country"). */
export function addressLine(a: { country: string; fields: Record<string, string> }, locale: string) {
  const dn = new Intl.DisplayNames([locale], { type: "region" });
  const f = a.fields || {};
  return [f.name, f.company, f.line1, f.line2, f.city, f.region, f.postal_code, dn.of(a.country)].filter(Boolean).join(", ");
}

export function useSavedAddresses() {
  return useLoad<{ items: { id: string; country: string; fields: Record<string, string>; last_used_at: string }[] }>("/me/addresses");
}

export function deleteSavedAddress(id: string) {
  return api(`/me/addresses/${id}`, { method: "DELETE" });
}

export function SavedAddressList({ items, action, onAction, pending }: {
  items: { id: string; country: string; fields: Record<string, string> }[];
  action: string; onAction: (id: string) => void; pending?: boolean;
}) {
  const { locale } = useApp();
  return (
    <ul className="stack plain" data-testid="saved-addresses">
      {items.map((a) => (
        <li key={a.id} className="panel flat row between">
          <span>{addressLine(a, locale)}</span>
          <button type="button" className="btn ghost" disabled={pending} onClick={() => onAction(a.id)}>{action}</button>
        </li>
      ))}
    </ul>
  );
}
