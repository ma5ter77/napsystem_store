"use client";

import Link from "next/link";
import { BeatCover } from "@/components/Art";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, RequireAuth, useLoad } from "@/components/ui";
import { date } from "@/lib/i18n";

function Library() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/library");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <div className="stack">
      <h1>{t("library.title")}</h1>
      {data.items.length === 0 ? <Empty action={<Link className="btn" href={`/${locale}/apps`}>{t("home.cta_apps")}</Link>}>{t("library.empty")}</Empty> : (
        <div className="grid">
          {data.items.map((a: any) => (
            <div key={a.id} className="stack">
              <Link className="card" href={`/${locale}/apps/${a.slug}`}>
                <div className="art"><BeatCover hue={a.cover_hue} seed={a.slug} title={a.title} /></div>
                <h3>{a.title}</h3>
              </Link>
              {a.access.has_access ? (
                <div className="row">
                  <Link className="btn small" href={`/${locale}/play/${a.slug}`}>{t("library.play")}</Link>
                  {a.access.paid_until && <span className="hint">{t("app.paid_until", { date: date(a.access.paid_until, locale) })}</span>}
                </div>
              ) : <span className="badge gray">{t("library.no_access")}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function LibraryPage() {
  return <div className="section"><RequireAuth><Library /></RequireAuth></div>;
}
