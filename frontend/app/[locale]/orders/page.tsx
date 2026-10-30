"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, Price, RequireAuth, StatusBadge, useLoad } from "@/components/ui";
import { date } from "@/lib/i18n";

function Orders() {
  const { t, locale } = useApp();
  const group = useSearchParams()?.get("group");
  const { data, error, loading, reload } = useLoad<any>("/hardware/orders");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const items = (data?.items ?? []).filter((o: any) => !group || o.group_ref === group);
  return (
    <div className="stack">
      <h1>{group ? t("orders.new_title") : t("orders.title")}</h1>
      {group && items.length > 1 && <div className="notice warn">{t("orders.independent", { count: items.length })}</div>}
      {items.length === 0 ? <Empty action={<Link className="btn" href={`/${locale}/devices`}>{t("cart.browse")}</Link>}>{t("orders.empty")}</Empty> :
        items.map((o: any) => (
          <Link key={o.id} href={`/${locale}/orders/${o.id}`} className="panel flat card">
            <div className="spread">
              <strong>{o.number}</strong>
              <span className="muted">{o.seller.name} · {date(o.created_at, locale)}</span>
            </div>
            <div className="status-line" style={{ marginTop: 8 }}>
              <StatusBadge ns="order_status" value={o.status} />
              <StatusBadge ns="payment_status" value={o.payment_status} />
              <StatusBadge ns="fulfillment_status" value={o.fulfillment_status} />
              <span>{o.total_minor !== null ? <Price minor={o.total_minor} currency={o.currency} /> : <span className="muted">{t("orders.total_pending")}</span>}</span>
            </div>
          </Link>
        ))}
    </div>
  );
}

export default function OrdersPage() {
  return <div className="section"><RequireAuth><Suspense><Orders /></Suspense></RequireAuth></div>;
}
