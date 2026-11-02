"use client";

// NAP Audio Studio (spec v2.1 M1): projects and the author's immutable source library.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/Providers";
import { Breadcrumbs, PageHead } from "@/components/nav";
import { RouterPanel } from "@/components/studio/Router";
import { SourceLibrary } from "@/components/studio/Sources";
import { timecode } from "@/components/studio/model";
import { Empty, ErrorBox, Loading, RequireAuth, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date } from "@/lib/i18n";

const LAYOUTS = [2, 3, 6, 8] as const;

function NewProject({ onCreated }: { onCreated: (id: string) => void }) {
  const { t } = useApp();
  const [f, setF] = useState({ title: "", bus_count: 2, sample_rate: 48000, bit_depth: 24 });
  const act = useAction();
  return (
    <form className="panel stack" onSubmit={async (e) => {
      e.preventDefault();
      const r = await act.run(() => api("/audio/projects", { body: f }));
      if (r) onCreated(r.id);
    }}>
      <h2>{t("studio.new_project")}</h2>
      <div className="form wide">
        <label className="field">{t("studio.title")}<input required maxLength={160} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
        <label className="field">{t("studio.buses")}<select value={f.bus_count} onChange={(e) => setF({ ...f, bus_count: Number(e.target.value) })}>
          {LAYOUTS.map((n) => <option key={n} value={n}>{t(`studio.layout_${n}`)}</option>)}</select>
          <span className="hint">{t("studio.buses_hint")}</span></label>
        <label className="field">{t("studio.sample_rate")}<select value={f.sample_rate} onChange={(e) => setF({ ...f, sample_rate: Number(e.target.value) })}>
          {[44100, 48000, 88200, 96000].map((r) => <option key={r} value={r}>{(r / 1000).toFixed(1)} kHz</option>)}</select></label>
        <label className="field">{t("studio.bit_depth")}<select value={f.bit_depth} onChange={(e) => setF({ ...f, bit_depth: Number(e.target.value) })}>
          <option value={16}>16-bit PCM</option><option value={24}>24-bit PCM</option><option value={32}>32-bit float</option></select></label>
      </div>
      <ErrorBox error={act.error} />
      <div><button className="btn" disabled={act.pending}>{t("studio.create")}</button></div>
    </form>
  );
}

function Projects() {
  const { t, locale } = useApp();
  const router = useRouter();
  const { data, error, loading, reload } = useLoad<any>("/audio/projects");
  const [showNew, setShowNew] = useState(false);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <section className="stack" aria-labelledby="projects-h">
      <div className="spread"><h2 id="projects-h">{t("studio.projects")}</h2>
        <button className="btn" onClick={() => setShowNew(!showNew)} aria-expanded={showNew}>{t("studio.new_project")}</button></div>
      {showNew && <NewProject onCreated={(id) => router.push(`/${locale}/developer/studio/${id}`)} />}
      {!data.items.length ? <Empty title={t("studio.no_projects_title")}>{t("studio.no_projects")}</Empty> : (
        <ul className="studio-projects">
          {data.items.map((p: any) => (
            <li key={p.id}>
              <Link href={`/${locale}/developer/studio/${p.id}`} className="studio-project">
                <span className="studio-project-title">{p.title}</span>
                <span className="muted">{t(`studio.layout_${p.bus_count}`)} · {(p.sample_rate / 1000).toFixed(1)} kHz / {p.bit_depth}-bit</span>
                <span className="muted">{timecode(p.duration_frames, p.sample_rate, false)} · {t("studio.revision_n", { n: p.revision })} · {date(p.updated_at, locale)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function StudioPage() {
  const { t, locale } = useApp();
  return (
    <div className="section"><RequireAuth role="developer">
      <div className="stack">
        <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}/developer`, label: t("nav.developer") }, { label: t("studio.name") }]} />
        <PageHead title={t("studio.name")} lead={t("studio.lead")} />
        <Projects />
        <SourceLibrary />
        <RouterPanel />
      </div>
    </RequireAuth></div>
  );
}
