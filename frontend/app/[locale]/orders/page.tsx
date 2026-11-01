"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, Price, RequireAuth, useLoad } from "@/components/ui";
import { OrderTimeline } from "@/components/OrderTimeline";
import { Breadcrumbs, PageHead } from "@/components/nav";
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
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { label: t("nav.orders") }]} />
      <PageHead title={group ? t("orders.new_title") : t("orders.title")} lead={t("orders.lead")} />
      {group && items.length > 1 && <div className="notice warn">{t("orders.independent", { count: items.length })}</div>}
      {items.length === 0 ? <Empty action={<Link className="btn" href={`/${locale}/devices`}>{t("cart.browse")}</Link>}>{t("orders.empty")}</Empty> :
        items.map((o: any) => (
          <Link key={o.id} href={`/${locale}/orders/${o.id}`} className="order-row" aria-label={`${t("orders.open")} ${o.number}`}>
            <div className="spread">
              <div><strong>{o.number}</strong> <span className="muted">· {o.seller.name} · {date(o.created_at, locale)}</span></div>
              <span className="price">{o.total_minor !== null ? <Price minor={o.total_minor} currency={o.currency} /> : <span className="muted">{t("orders.total_pending")}</span>}</span>
            </div>
            <OrderTimeline order={o} showNext={false} />
          </Link>
        ))}
    </div>
  );
}

export default function OrdersPage() {
  return <div className="section"><RequireAuth><Suspense><Orders /></Suspense></RequireAuth></div>;
}
