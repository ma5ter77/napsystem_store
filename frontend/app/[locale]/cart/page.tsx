"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useApp } from "@/components/Providers";
import { availabilityText } from "@/components/cards";
import { Empty, ErrorBox, Loading, Price, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

export default function CartPage() {
  const { t, locale, me, refreshCart } = useApp();
  const router = useRouter();
  const { data, error, loading, reload, setData } = useLoad<any>("/hardware/cart");
  const act = useAction();
  if (loading && !data) return <div className="section"><Loading /></div>;
  if (error) return <div className="section"><ErrorBox error={error} onRetry={reload} /></div>;
  const cart = data!;
  async function setQty(sku: string, q: number) {
    const r = await act.run(() => api(`/hardware/cart/items/${sku}`, { method: "PUT", body: { sku_id: sku, quantity: q } }));
    if (r) { setData(r); refreshCart(); }
  }
  return (
    <div className="section stack">
      <h1>{t("cart.title")}</h1>
      {cart.groups.length === 0 ? (
        <Empty action={<Link className="btn" href={`/${locale}/devices`}>{t("cart.browse")}</Link>}>{t("cart.empty")}</Empty>
      ) : (
        <>
          {cart.separate_orders && <div className="notice warn">{t("cart.separate_orders", { count: cart.groups.length })}</div>}
          {cart.groups.map((g: any, gi: number) => (
            <section key={gi} className="panel stack">
              <div className="spread">
                <h2>{t("cart.order_from", { seller: g.seller.name })}</h2>
                <span className="muted">{t("cart.goods")}: <Price minor={g.goods_minor} currency={g.currency} /></span>
              </div>
              {g.needs_custom_quote && <div className="notice">{t("cart.custom_quote")}</div>}
              <div className="scroll-x"><table>
                <thead><tr><th>{t("cart.item")}</th><th>{t("cart.price")}</th><th>{t("cart.qty")}</th><th>{t("cart.availability")}</th></tr></thead>
                <tbody>{g.items.map((it: any) => (
                  <tr key={it.sku_id}>
                    <td><Link href={`/${locale}/devices/${it.product_slug}`}>{it.title}</Link><div className="hint">{it.code}</div></td>
                    <td><Price minor={it.unit_price_minor} currency={g.currency} /></td>
                    <td><input type="number" min={0} max={99} defaultValue={it.quantity} style={{ width: 80 }} aria-label={t("cart.qty")}
                               onBlur={(e) => Number(e.target.value) !== it.quantity && setQty(it.sku_id, Number(e.target.value))} /></td>
                    <td>{availabilityText(it.availability, t)}</td>
                  </tr>))}
                </tbody>
              </table></div>
            </section>
          ))}
          <p className="hint">{t("cart.shipping_later")}</p>
          <ErrorBox error={act.error} />
          {me ? (
            <button className="btn" disabled={act.pending} onClick={async () => {
              const r = await act.run(() => api("/hardware/checkout", { method: "POST" }));
              if (r) { refreshCart(); router.push(`/${locale}/orders?group=${r.orders[0].group_ref}`); }
            }}>{cart.separate_orders ? t("cart.checkout_many", { count: cart.groups.length }) : t("cart.checkout")}</button>
          ) : <Link className="btn" href={`/${locale}/login?next=/${locale}/cart`}>{t("cart.login_to_checkout")}</Link>}
        </>
      )}
    </div>
  );
}
