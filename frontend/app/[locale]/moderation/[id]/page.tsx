"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

function Review() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>(`/moderation/versions/${id}`);
  const [comment, setComment] = useState("");
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const v = data.version;
  const decide = async (decision: string) => { if (await act.run(() => api(`/moderation/versions/${id}/decision`, { body: { decision, comment } }))) { setComment(""); reload(); } };
  return (
    <div className="stack">
      <Link href={`/${locale}/moderation`}>{t("mod.title")}</Link>
      <div className="spread"><h1>{data.app.slug} v{v.version_label}</h1><StatusBadge ns="version_state" value={v.state} /></div>
      {data.integrity && data.integrity.length > 0 && <div className="notice bad">{t("mod.integrity")}: {data.integrity.join(", ")}</div>}
      <div className="two">
        {["ru", "en"].map((l) => v.metadata[l] && (
          <div key={l} className="panel stack" lang={l}><strong>{l.toUpperCase()}</strong><h3>{v.metadata[l].title}</h3><p>{v.metadata[l].subtitle}</p><p className="hint">{v.metadata[l].description}</p></div>))}
      </div>
      <p>{t("dev.rights_state", { ok: v.rights_confirmed ? t("common.yes") : t("common.no") })} · {t("app.compat")}: {(v.requirements.required_capabilities ?? []).map((c: string) => t(`caps.${c}`)).join(", ")}</p>
      {data.sessions.map((s: any) => (
        <section key={s.key} className="panel stack">
          <h3>{s.titles[locale] ?? Object.values(s.titles)[0]} <span className="hint">({s.key})</span></h3>
          <div className="lr">
            <div><div className="hint">{t("dev.source")}</div><audio controls preload="none" src={s.source_url} style={{ width: "100%" }} /></div>
            <div><div className="hint">{t("dev.result")}</div><audio controls preload="none" src={s.delivery_url} style={{ width: "100%" }} /></div>
          </div>
          {s.qa && <div className="hint">{typeof s.qa.layer === "object" ? `${s.qa.layer.left_hz}/${s.qa.layer.right_hz} Hz, Δ${s.qa.layer.beat_hz} · ` : ""}TP {String(s.qa.levels.true_peak_master_dbtp)} dBTP · {String(s.qa.levels.output_integrated_lufs)} LUFS · {s.params.profile.slug} v{s.params.profile.version}</div>}
        </section>
      ))}
      <details className="panel flat"><summary>manifest ({v.manifest_sha256?.slice(0, 16)}…)</summary><pre style={{ whiteSpace: "pre-wrap", fontSize: ".8rem" }}>{JSON.stringify(data.manifest, null, 2)}</pre></details>
      {v.moderation.map((d: any, i: number) => <div key={i} className="notice"><StatusBadge ns="decision" value={d.decision} /> {d.comment}</div>)}
      <div className="panel stack">
        <label className="field">{t("mod.comment")}<textarea value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        <div className="row">
          {v.state === "submitted" && <><button className="btn" onClick={() => decide("approve")}>{t("mod.approve")}</button>
            <button className="btn danger" onClick={() => decide("reject")}>{t("mod.reject")}</button></>}
          <button className="btn ghost" onClick={() => decide("comment")} disabled={!comment}>{t("mod.comment_only")}</button>
        </div>
        <ErrorBox error={act.error} />
      </div>
    </div>
  );
}

export default function ReviewPage() {
  return <div className="section"><RequireAuth role={["moderator", "admin"]}><Review /></RequireAuth></div>;
}
