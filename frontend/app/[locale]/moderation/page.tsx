"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ManageApps, ManageProducts, ManageReviews } from "@/components/ModeratorManage";
import { useApp } from "@/components/Providers";
import { ModerationReviews } from "@/components/Reviews";
import { Empty, ErrorBox, Loading, RequireAuth, useLoad } from "@/components/ui";
import { date } from "@/lib/i18n";

function Queue() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/moderation/queue");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <h2>{t("mod.apps_title")}</h2>
      {!data.items.length ? <Empty>{t("mod.empty")}</Empty> : data.items.map((x: any) => (
        <Link key={x.version.id} href={`/${locale}/moderation/${x.version.id}`} className="panel flat card">
          <div className="spread"><strong>{x.app.title} <span className="hint">v{x.version.version_label}</span></strong><span className="hint">{x.app.author} · {date(x.version.submitted_at, locale, true)}</span></div>
          <div className="hint">{x.app.slug} · {t("dev.sessions_count", { count: x.version.sessions.length })} · {!x.app.price_set ? t("mod.no_price_yet") : x.app.auto_release ? t("mod.goes_live_on_approve") : t("mod.author_publishes")}</div>
        </Link>
      ))}
    </div>
  );
}

// NAP-022: supplier products waiting for a decision
function ProductQueue() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/moderation/products");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <h2>{t("mod.products_title")}</h2>
      {!data.items.length ? <Empty>{t("mod.products_empty")}</Empty> : data.items.map((x: any) => (
        <Link key={x.id} href={`/${locale}/moderation/products/${x.id}`} className="panel flat card">
          <div className="spread"><strong>{x.title ?? `${x.brand} ${x.model}`}</strong><span className="hint">{x.seller}{x.submitted_at ? ` · ${date(x.submitted_at, locale, true)}` : ""}</span></div>
          <div className="hint">{x.slug} · {t("sp.col_skus")}: {x.skus} · {x.languages.map((l: string) => l.toUpperCase()).join(", ")}</div>
        </Link>
      ))}
    </div>
  );
}

// NAP-084: the moderator's workspace — the decision queue plus management of every app, product and review
const TABS = ["queue", "apps", "products", "reviews"] as const;

function Workspace() {
  const { t, locale, me } = useApp();
  const router = useRouter();
  const tab = (useSearchParams()?.get("tab") as (typeof TABS)[number]) || "queue";
  const roles = me?.roles ?? [];
  return (
    <div className="stack">
      <div className="spread"><h1>{t("mod.title")}</h1>
        {(roles.includes("content_editor") || roles.includes("admin")) && <Link className="btn ghost" href={`/${locale}/content`}>{t("nav.content")}</Link>}</div>
      <div className="tabs" role="tablist">{TABS.map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => router.replace(`/${locale}/moderation?tab=${x}`)}>{t(`mm.tab_${x}`)}</button>)}</div>
      {tab === "queue" && <div className="stack"><Queue /><ProductQueue /><ModerationReviews /></div>}
      {tab === "apps" && <ManageApps />}
      {tab === "products" && <ManageProducts />}
      {tab === "reviews" && <ManageReviews />}
    </div>
  );
}

export default function ModerationPage() {
  return <div className="section"><RequireAuth role={["moderator", "admin"]}><Suspense><Workspace /></Suspense></RequireAuth></div>;
}
