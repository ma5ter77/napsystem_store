"use client";

import { Breadcrumbs, PageHead } from "@/components/nav";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useApp } from "@/components/Providers";
import { DeveloperReviews } from "@/components/Reviews";
import { SellerProducts } from "@/components/SellerProducts";
import { Empty, ErrorBox, Loading, Price, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

function Orders() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/seller/orders");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  if (!data.items.length) return <Empty>{t("seller.no_orders")}</Empty>;
  return (
    <div className="scroll-x"><table>
      <thead><tr><th>{t("seller.order")}</th><th>{t("account.date")}</th><th>{t("seller.destination")}</th><th>{t("account.status")}</th><th>{t("orders.total")}</th></tr></thead>
      <tbody>{data.items.map((o: any) => (
        <tr key={o.id}>
          <td><Link href={`/${locale}/seller/orders/${o.id}`}>{o.number}</Link></td>
          <td>{date(o.created_at, locale)}</td>
          <td>{o.address?.country ?? "—"}</td>
          <td className="status-line"><StatusBadge ns="order_status" value={o.status} /><StatusBadge ns="fulfillment_status" value={o.fulfillment_status} /></td>
          <td>{o.total_minor !== undefined ? <Price minor={o.total_minor} currency={o.currency} /> : "—"}</td>
        </tr>))}</tbody>
    </table></div>
  );
}

function Inventory() {
  const { t } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/seller/inventory");
  const [delta, setDelta] = useState<Record<string, number>>({});
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <p className="hint">{t("seller.inv_hint")}</p>
      <div className="scroll-x"><table>
        <thead><tr><th>SKU</th><th>{t("seller.on_hand")}</th><th>{t("seller.reserved")}</th><th>{t("seller.allocated")}</th><th>{t("seller.available")}</th><th>{t("seller.adjust")}</th></tr></thead>
        <tbody>{data.items.map((i: any) => (
          <tr key={i.sku_id}><td>{i.code}<div className="hint">{i.product_slug}</div></td>
            {i.made_to_order ? <td colSpan={4}>{t("hw.made_to_order_short")}</td> : <><td>{i.on_hand}</td><td>{i.reserved}</td><td>{i.allocated}</td><td><strong>{i.available}</strong></td></>}
            <td>{!i.made_to_order && <div className="row"><input type="number" style={{ width: 80 }} value={delta[i.sku_id] ?? 0} aria-label={t("seller.adjust")} onChange={(e) => setDelta({ ...delta, [i.sku_id]: Number(e.target.value) })} />
              <button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/seller/inventory/${i.sku_id}/adjust`, { body: { delta: delta[i.sku_id] ?? 0, reason: "manual count" } }))) reload(); }}>{t("common.save")}</button></div>}</td></tr>))}
        </tbody></table></div>
      <ErrorBox error={act.error} />
    </div>
  );
}

function Seller() {
  const { t, locale, me } = useApp();
  const router = useRouter();
  const tab = useSearchParams()?.get("tab") || "orders";
  const isSeller = me?.roles.includes("seller");
  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { label: isSeller ? t("seller.title") : t("seller.fulfillment_title") }]} />
      <PageHead title={isSeller ? t("seller.title") : t("seller.fulfillment_title")} lead={isSeller ? t("seller.lead") : t("seller.fulfillment_lead")} />
      {isSeller && <div className="tabs" role="tablist">{["orders", "products", "inventory", "reviews"].map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/${locale}/seller?tab=${x}`)}>{t(`seller.tab_${x}`)}</button>)}</div>}
      {tab === "products" && isSeller ? <SellerProducts /> : tab === "inventory" && isSeller ? <Inventory /> : tab === "reviews" && isSeller ? <DeveloperReviews scope="seller" /> : <Orders />}
    </div>
  );
}

export default function SellerPage() {
  return <div className="section"><RequireAuth role={["seller", "fulfillment"]}><Suspense><Seller /></Suspense></RequireAuth></div>;
}
