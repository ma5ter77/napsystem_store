"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";

/** NAP-027 / NAP-062: reports one app-page view. Nothing is stored in the browser (no cookie, no localStorage):
 *  the server counts a guest by a daily hash of address and browser inside a HyperLogLog sketch. */
export function ViewBeacon({ slug }: { slug: string }) {
  useEffect(() => {
    api(`/catalog/apps/${slug}/view`, { method: "POST" }).catch(() => {});
  }, [slug]);
  return null;
}
