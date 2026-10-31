"use client";

import Link from "next/link";
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
      <h1>{t("mod.title")}</h1>
      {!data.items.length ? <Empty>{t("mod.empty")}</Empty> : data.items.map((x: any) => (
        <Link key={x.version.id} href={`/${locale}/moderation/${x.version.id}`} className="panel flat card">
          <div className="spread"><strong>{x.app.slug} v{x.version.version_label}</strong><span className="hint">{x.app.author} · {date(x.version.submitted_at, locale, true)}</span></div>
          <div className="hint">{t("dev.sessions_count", { count: x.version.sessions.length })}</div>
        </Link>
      ))}
    </div>
  );
}

export default function ModerationPage() {
  return <div className="section"><RequireAuth role={["moderator", "admin"]}><div className="stack"><Queue /><ModerationReviews /></div></RequireAuth></div>;
}
