"use client";

import { useParams } from "next/navigation";
import { useApp } from "@/components/Providers";
import { Breadcrumbs } from "@/components/nav";
import { Editor, type Project } from "@/components/studio/Editor";
import type { Profile } from "@/components/studio/Render";
import { ErrorBox, Loading, RequireAuth, useLoad } from "@/components/ui";

function Studio({ id }: { id: string }) {
  const { t, locale } = useApp();
  const project = useLoad<Project>(`/audio/projects/${id}`);
  const profiles = useLoad<{ items: Profile[] }>("/audio/routing-profiles");
  if ((project.loading && !project.data) || (profiles.loading && !profiles.data)) return <Loading />;
  if (project.error || profiles.error) return <ErrorBox error={project.error || profiles.error} onRetry={() => { project.reload(); profiles.reload(); }} />;
  const p = project.data!;
  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}/developer`, label: t("nav.developer") },
        { href: `/${locale}/developer/studio`, label: t("studio.name") }, { label: p.title }]} />
      <h1 className="sr-only">{p.title}</h1>
      {/* remount on reload so a conflict resolution starts from the server state */}
      <Editor key={`${p.id}:${p.revision}`} project={p} profiles={profiles.data!.items} onReload={project.reload} />
    </div>
  );
}

export default function StudioProjectPage() {
  const { id } = useParams<{ id: string }>();
  return <div className="section studio-section"><RequireAuth role="developer"><Studio id={id} /></RequireAuth></div>;
}
