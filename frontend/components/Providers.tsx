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
  const t = useMemo(() => translator(locale), [locale]);
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
