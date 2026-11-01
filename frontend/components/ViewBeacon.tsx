"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";

/** NAP-027: reports one app-page view. The visitor key is a random id kept in this browser (no cookie);
 *  the server hashes it into a HyperLogLog sketch and never stores it. */
function visitorId(): string {
  const make = () => (crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`);
  try {
    let v = localStorage.getItem("nap:visitor");
    if (!v) { v = make(); localStorage.setItem("nap:visitor", v); }
    return v;
  } catch { return make(); }
}

export function ViewBeacon({ slug }: { slug: string }) {
  useEffect(() => {
    api(`/catalog/apps/${slug}/view`, { body: { visitor: visitorId() } }).catch(() => {});
  }, [slug]);
  return null;
}
