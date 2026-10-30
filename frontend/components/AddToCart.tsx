"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "./Providers";
import { ErrorBox, useAction } from "./ui";

export function AddToCart({ skuId, disabled }: { skuId: string; disabled?: boolean }) {
  const { t, locale, refreshCart } = useApp();
  const [qty, setQty] = useState(1);
  const [done, setDone] = useState(false);
  const act = useAction();
  return (
    <div className="stack">
      <div className="row">
        <label className="field" style={{ width: 90 }}><span className="sr-only">{t("cart.qty")}</span>
          <input type="number" min={1} max={99} value={qty} onChange={(e) => setQty(Number(e.target.value) || 1)} aria-label={t("cart.qty")} /></label>
        <button className="btn" disabled={disabled || act.pending} onClick={async () => {
          const r = await act.run(() => api("/hardware/cart/items", { body: { sku_id: skuId, quantity: qty } }));
          if (r) { setDone(true); refreshCart(); }
        }}>{t("cart.add")}</button>
        {done && <Link href={`/${locale}/cart`}>{t("cart.added")}</Link>}
      </div>
      <ErrorBox error={act.error} />
    </div>
  );
}
