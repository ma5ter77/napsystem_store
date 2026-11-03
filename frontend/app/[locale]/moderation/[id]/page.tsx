"use client";

import { Breadcrumbs, PageHead } from "@/components/nav";
import Link from "next/link";
import { ChannelDiagram } from "@/components/ChannelDiagram";
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
  const [done, setDone] = useState<{ decision: string; next: string | null; state?: string; blockers?: string[] } | null>(null);
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const v = data.version;
  // NAP-082: confirm the decision and offer the next version waiting in the queue
  const decide = async (decision: string) => {
    const r = await act.run(() => api<any>(`/moderation/versions/${id}/decision`, { body: { decision, comment } }));
    if (r) {
      setComment(""); reload();
      const q = await api<any>("/moderation/queue").catch(() => ({ items: [] }));
      setDone({ decision, next: q.items.find((x: any) => x.version.id !== id)?.version.id ?? null, state: r.state, blockers: r.release_blockers });
    }
  };
  return (
    <div className="stack">
      <Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { href: `/${locale}/moderation`, label: t("mod.title") }, { label: data.app.title }]} />
      <div className="spread"><div><h1>{data.app.title} <span className="hint">v{v.version_label}</span></h1><div className="hint">{data.app.slug} · {data.app.author}</div></div><StatusBadge ns="version_state" value={v.state} /></div>
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
          {s.qa && <div className="hint">{typeof s.qa.layer === "object" ? `${s.qa.layer.left_hz}/${s.qa.layer.right_hz} Hz, Δ${s.qa.layer.beat_hz} · ` : ""}TP {String(s.qa.levels?.true_peak_master_dbtp)} dBTP · {String(s.qa.levels?.output_integrated_lufs)} LUFS · {s.studio
            ? t("lay.studio_note", { project: s.studio.project_title, rev: s.studio.revision, profile: s.studio.routing_profile_name })
            : `${s.profile?.slug ?? ""} v${s.profile?.version ?? ""}`}</div>}
          {/* NAP-113: every version the listener can get is heard and checked here */}
          {(s.layouts ?? []).map((l: any) => (
            <details key={l.layout} className="panel flat" data-testid={`mod-layout-${l.layout}`}>
              <summary><strong>{t(`layout.${l.layout}`)}</strong> <span className="hint">· {t("mod.layout_channels", { n: l.channels })} · {l.qa?.verification?.passed ? t("mod.layout_ok") : t("mod.layout_bad")}</span></summary>
              <div className="stack" style={{ marginTop: 10 }}>
                <audio controls preload="none" src={l.delivery_url} style={{ width: "100%" }} />
                <p className="hint">{t("lay.listen_note", { n: l.channels })}</p>
                <ChannelDiagram layout={l.layout} idPrefix={`mod-${s.key}-${l.layout}`}
                  own={(l.qa?.channels ?? []).filter((c: any) => c.uploaded).map((c: any) => c.channel)} />
                {l.studio && <p className="hint">{t("lay.studio_note", { project: l.studio.project_title, rev: l.studio.revision, profile: l.studio.routing_profile_name })}</p>}
                {l.qa && <div className="scroll-x"><table className="channel-map">
                  <thead><tr><th>{t("lay.channel")}</th><th>{t("lay.source")}</th><th>{t("terms.true_peak.name")}</th><th>RMS</th></tr></thead>
                  <tbody>{l.qa.channels.map((c: any) => (
                    <tr key={c.channel}><th scope="row"><span className="ch-code">{c.channel}</span> {t(`ch.${c.channel}`)}</th>
                      <td>{c.source === "studio" ? t("lay.from_studio") : c.uploaded ? t("lay.own_file", { name: l.own_files?.[c.channel] ?? "" }) : t(`lay.from_main.${c.channel}`)}</td>
                      {typeof c.true_peak_dbtp === "number" ? <><td>{c.true_peak_dbtp} dBTP</td><td>{c.rms_dbfs} dBFS</td></> : <><td>{t("studio.silent")}</td><td>—</td></>}</tr>))}</tbody></table></div>}
              </div>
            </details>))}
        </section>
      ))}
      <details className="panel flat"><summary>manifest ({v.manifest_sha256?.slice(0, 16)}…)</summary><pre style={{ whiteSpace: "pre-wrap", fontSize: ".8rem" }}>{JSON.stringify(data.manifest, null, 2)}</pre></details>
      {v.moderation.map((d: any, i: number) => <div key={i} className="notice"><StatusBadge ns="decision" value={d.decision} /> {d.comment}</div>)}
      {done && (
        <div className={`notice ${done.decision === "reject" ? "warn" : "ok"}`} role="status">
          <strong>{done.state === "published" ? t("mod.done_published") : t(`mod.done_${done.decision}`)}</strong>{" "}
          {done.state === "published" && <Link href={`/${locale}/apps/${data.app.slug}`}>{t("mod.open_in_store")}</Link>}
          {done.state === "approved" && (done.blockers ?? []).includes("price") && t("mod.waits_for_price")}
          {done.state === "approved" && !(done.blockers ?? []).includes("price") && (done.blockers ?? []).includes("manual_release") && t("mod.waits_for_author")}
          {" "}{done.decision !== "comment" && t("mod.done_notified")}
          <div className="row" style={{ marginTop: 8 }}>
            {done.next ? <Link className="btn small" href={`/${locale}/moderation/${done.next}`}>{t("mod.next")}</Link> : <span className="hint">{t("mod.queue_empty_now")}</span>}
            <Link className="btn ghost small" href={`/${locale}/moderation`}>{t("mod.back_to_queue")}</Link>
          </div>
        </div>
      )}
      <div className="panel stack">
        <label className="field">{t("mod.comment")}<textarea value={comment} onChange={(e) => setComment(e.target.value)} /></label>
        <div className="row">
          {v.state === "submitted" && <><button className="btn" disabled={act.pending} onClick={() => decide("approve")}>{t("mod.approve")}</button>
            <button className="btn danger" disabled={act.pending} onClick={() => decide("reject")}>{t("mod.reject")}</button></>}
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
