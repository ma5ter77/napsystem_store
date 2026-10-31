"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Locale, T, translator } from "@/lib/i18n";

export interface Me { id: string; email: string; display_name: string; locale: Locale; roles: string[] }

interface Ctx {
  locale: Locale;
  t: T;
  me: Me | null | undefined; // undefined = loading
  refreshMe: () => Promise<void>;
  cartCount: number;
  refreshCart: () => Promise<void>;
}

const C = createContext<Ctx | null>(null);

export function Providers({ locale, children }: { locale: Locale; children: React.ReactNode }) {
  // NAP-084: texts rewritten by the content editor are merged over the built-in dictionary
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    api<{ texts: Record<string, string> }>(`/content/texts?locale=${locale}`)
      .then((r) => { if (alive) setOverrides(r.texts ?? {}); }).catch(() => {});
    return () => { alive = false; };
  }, [locale]);
  const t = useMemo(() => translator(locale, overrides), [locale, overrides]);
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [cartCount, setCart] = useState(0);
  const refreshMe = useCallback(async () => {
    try { setMe(await api<Me>("/me")); } catch { setMe(null); }
  }, []);
  const refreshCart = useCallback(async () => {
    try { setCart((await api<{ items: number }>("/hardware/cart")).items); } catch { setCart(0); }
  }, []);
  useEffect(() => { refreshMe(); refreshCart(); }, [refreshMe, refreshCart]);
  return <C.Provider value={{ locale, t, me, refreshMe, cartCount, refreshCart }}>{children}</C.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(C);
  if (!c) throw new Error("Providers missing");
  return c;
}
