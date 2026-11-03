"use client";

// Export (spec §2.6–2.7): pick a routing profile, render on the server, watch progress, read the verification
// report and download the exact file that was verified.
import { Term } from "@/components/Term";
import { useCallback, useEffect, useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, StatusBadge, useAction } from "@/components/ui";
import { api, uid } from "@/lib/api";
import { date } from "@/lib/i18n";
import type { State } from "./model";
import { toDb } from "./Router";

export type Profile = { key: string; version: number; name: string; layout: string; channels: string[];
  matrix: Record<string, number>[]; note: string; builtin: boolean; custom?: boolean;
  options?: { mute?: boolean; invert?: boolean; delay_ms?: number }[] };

const KNOWN_ERRORS = new Set(["clipping", "empty_project", "missing_source", "non_finite", "verify_failed", "verification_failed",
  "canceled", "too_many_channels", "decode_failed", "too_large_for_wav", "render_error", "lease_expired", "flac_needs_integer", "encode_failed"]);
const ACTIVE = new Set(["queued", "decoding", "rendering", "verifying"]);
const DEFAULT_FOR_BUSES: Record<number, string> = { 2: "HEADPHONES_2_0", 3: "NAP_ACOUSTIC_3_0", 6: "NAP_CHAIR_5_1", 8: "NAP_PRO_7_1" };

function bytes(n: number) {
  return n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(2)} GB` : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`;
}

export function RenderPanel({ project, profiles, state, unsaved, onClipChange }: { project: { id: string; bus_count: number; sample_rate: number; bit_depth: number };
  profiles: Profile[]; state: State; unsaved: boolean; onClipChange: (v: "fail" | "hard_clip") => void }) {
  const { t, locale } = useApp();
  const [profile, setProfile] = useState(DEFAULT_FOR_BUSES[project.bus_count] ?? "HEADPHONES_2_0");
  const [format, setFormat] = useState<"wav" | "flac">("wav");
  const flacOk = project.bit_depth !== 32;
  const [jobs, setJobs] = useState<any[]>([]);
  const act = useAction();
  const load = useCallback(() => api(`/audio/projects/${project.id}/renders`).then((r) => setJobs(r.items)).catch(() => {}), [project.id]);
  useEffect(() => { load(); }, [load]);
  const active = jobs.some((j) => ACTIVE.has(j.state));
  useEffect(() => {
    if (!active) return;
    const id = setInterval(load, 1000);
    return () => clearInterval(id);
  }, [active, load]);

  const p = profiles.find((x) => x.key === profile);
  const used = new Set(state.tracks.filter((tr) => tr.clips.length).map((tr) => tr.bus));
  const dropped = p ? [...used].filter((b) => !p.matrix.some((row) => b in row)) : [];

  return (
    <section className="studio-render panel stack" aria-labelledby="render-h">
      <h2 id="render-h">{t("studio.export")}</h2>
      <div className="studio-render-grid">
        <div className="stack">
          <label className="field">{t("studio.profile")}
            <select value={profile} onChange={(e) => setProfile(e.target.value)}>
              {profiles.map((x) => <option key={x.key} value={x.key}>{x.name} — {x.layout}{x.custom ? ` · ${t("studio.router_own_v", { n: x.version })}` : ""}</option>)}</select></label>
          {p && <>
            <ol className="studio-channels" aria-label={t("studio.channel_order")}>
              {p.channels.map((ch, i) => (
                <li key={ch}><strong>{ch}</strong><span className="muted">{p.options?.[i]?.mute ? t("studio.router_muted") : (Object.entries(p.matrix[i]).map(([bus, g]) =>
                  `${t(`studio.bus.${bus}`)}${g !== 1 ? ` ${toDb(g)} dB` : ""}`).join(" + ") || t("studio.silent"))}
                  {p.options?.[i]?.invert ? " · Ø" : ""}{p.options?.[i]?.delay_ms ? ` · +${p.options[i].delay_ms} ms` : ""}</span></li>
              ))}
            </ol>
            <p className="hint">{p.note}</p>
          </>}
          <fieldset className="studio-formats">
            <legend>{t("studio.format")}</legend>
            <label className="studio-check"><input type="radio" name="fmt" checked={format === "wav"} onChange={() => setFormat("wav")} />{t("studio.fmt_wav")}</label>
            <label className="studio-check"><input type="radio" name="fmt" checked={format === "flac"} disabled={!flacOk} onChange={() => setFormat("flac")} />{t("studio.fmt_flac")}</label>
            <span className="hint">{flacOk ? t("studio.fmt_hint") : t("studio.fmt_flac_float")}</span>
          </fieldset>
          {dropped.length > 0 && <div className="notice warn">{t("studio.profile_drops", { buses: dropped.map((b) => t(`studio.bus.${b}`)).join(", ") })}</div>}
          <label className="studio-check"><input type="checkbox" checked={state.output.on_clip === "hard_clip"}
            onChange={(e) => onClipChange(e.target.checked ? "hard_clip" : "fail")} />{t("studio.allow_clip")}</label>
          <p className="hint">{t("studio.allow_clip_hint")}</p>
          {unsaved && <p className="hint">{t("studio.render_waits_save")}</p>}
          <ErrorBox error={act.error} />
          <div><button className="btn" disabled={act.pending || unsaved || active}
            onClick={() => act.run(async () => { await api(`/audio/projects/${project.id}/renders`, { body: { profile, format: flacOk ? format : "wav" }, headers: { "Idempotency-Key": uid() } }); await load(); })}>
            {t("studio.render")}</button></div>
        </div>
        <div className="stack">
          <h3>{t("studio.renders")}</h3>
          {!jobs.length ? <p className="hint">{t("studio.no_renders")}</p> : jobs.map((j) => <Job key={j.id} j={j} locale={locale} onChange={load} />)}
        </div>
      </div>
    </section>
  );
}

// NAP-110: the verified export becomes a track of one of the author's apps as it is: a stereo export is the
// headphones track, a 3.0 / 5.1 / 7.1 export the speaker or chair version of the session made from this project.
const STORE_LAYOUT: Record<string, string> = { stereo: "headphones", "3.0": "acoustic_3_0", "5.1(side)": "surround_5_1", "7.1": "surround_7_1" };

function ToApp({ j }: { j: any }) {
  const { t, locale } = useApp();
  const [apps, setApps] = useState<any[] | null>(null);
  const [app, setApp] = useState("");
  const [sent, setSent] = useState<any>(null);
  const act = useAction();
  const layout = STORE_LAYOUT[j.artifact.layout];
  useEffect(() => { api<any>("/developer/apps").then((r) => setApps(r.items)).catch(() => setApps([])); }, []);
  if (!layout) return <p className="hint" data-testid="to-app">{t("studio.to_app_unsupported")}</p>;
  if (apps && !apps.length) return <p className="hint" data-testid="to-app">{t("studio.to_app_no_apps")} <a href={`/${locale}/developer`}>{t("dev.go_apps")}</a></p>;
  const target = app || apps?.[0]?.id || "";
  const send = async () => {
    const r = await act.run(() => api<any>(`/audio/render-jobs/${j.id}/to-app`, { body: { app_id: target }, headers: { "Idempotency-Key": uid() } }));
    if (r) setSent({ ...r, app: target });
  };
  return (
    <div className="stack studio-to-app" data-testid="to-app">
      <p className="hint">{t(layout === "headphones" ? "studio.to_app_hp" : "studio.to_app_layout", { layout: t(`layout.${layout}`) })}</p>
      <div className="row">
        <label className="field" style={{ flex: "1 1 220px" }}>{t("studio.to_app_label")}
          <select value={target} onChange={(e) => setApp(e.target.value)} disabled={!apps}>
            {(apps ?? []).map((a: any) => <option key={a.id} value={a.id}>{a.title}</option>)}</select></label>
        <button className="btn small" style={{ alignSelf: "end" }} disabled={act.pending || !target} onClick={send}>{t("studio.to_app")}</button>
      </div>
      {sent && <p className="notice ok" role="status">{t("studio.to_app_sent")} <a href={`/${locale}/developer/apps/${sent.app}?tab=audio`}>{t("studio.to_app_open")}</a></p>}
      <ErrorBox error={act.error} />
    </div>
  );
}

function Job({ j, locale, onChange }: { j: any; locale: "ru" | "en"; onChange: () => void }) {
  const { t } = useApp();
  const act = useAction();
  const a = j.artifact;
  return (
    <article className="studio-job">
      <div className="spread">
        <span><strong>{j.profile_name ?? j.profile}</strong>{j.profile?.startsWith("u-") ? <span className="muted"> · {t("studio.router_version", { n: j.profile_version })}</span> : null} <span className="muted">· {t("studio.snapshot_n", { n: j.revision })} · {date(j.created_at, locale, true)}</span></span>
        <StatusBadge ns="render_state" value={j.state} />
      </div>
      {ACTIVE.has(j.state) && <>
        <div className="progress" role="progressbar" aria-valuenow={Math.round(j.progress * 100)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${j.progress * 100}%` }} /></div>
        <div><button className="btn small ghost" disabled={act.pending}
          onClick={() => act.run(async () => { await api(`/audio/render-jobs/${j.id}/cancel`, { method: "POST" }); onChange(); })}>{t("studio.cancel")}</button></div>
      </>}
      {j.state === "failed" && <div className="notice bad">
        <strong>{KNOWN_ERRORS.has(j.error_code) ? t(`studio.err.${j.error_code}`) : j.error_code}</strong>
        {j.error_code === "clipping" && j.error_detail?.channels && <ul>{Object.entries(j.error_detail.channels).map(([ch, v]: any) =>
          <li key={ch}>{t("studio.clip_at", { ch, n: v.samples, at: v.first_frame })}</li>)}</ul>}
        {j.error_code !== "clipping" && j.error_detail?.message && <p className="hint">{String(j.error_detail.message)}</p>}
      </div>}
      {a && <>
        <dl className="kv">
          <dt>{t("studio.format")}</dt><dd>{a.format === "flac" ? "FLAC" : a.report?.container === "RF64" ? "RF64 (WAV > 4 GB)" : "WAV"} · {a.layout} · {a.sample_rate} Hz · {a.bit_depth === 32 ? "32-bit float" : `${a.bit_depth}-bit PCM`} · {bytes(a.size_bytes)}</dd>
          <dt>{t("studio.channel_order")}</dt><dd>{a.channel_order.join(" ")}</dd>
          <dt>SHA-256</dt><dd className="studio-hash">{a.sha256}</dd>
          <dt>{t("studio.verification")}</dt><dd>{a.report?.verification?.passed ? t("studio.verified") : t("studio.not_verified")}</dd>
          {a.format === "flac" && <><dt>{t("studio.lossless")}</dt><dd>{a.report?.flac?.samples_identical_to_wav ? t("studio.lossless_ok") : t("studio.not_verified")}</dd></>}
          {a.report?.hard_clipped_samples > 0 && <><dt>{t("studio.clipped")}</dt><dd>{a.report.hard_clipped_samples}</dd></>}
        </dl>
        <table className="studio-meter">
          <thead><tr><th>{t("studio.ch")}</th><th>{t("studio.peak")}</th><th>{t("studio.true_peak")}<Term k="true_peak" /></th><th>RMS</th></tr></thead>
          <tbody>{a.report?.channels?.map((c: any) => (
            <tr key={c.channel}><td>{c.channel}</td><td>{c.silent ? t("studio.silent") : `${c.sample_peak_dbfs} dBFS`}</td>
              <td>{typeof c.true_peak_dbtp === "number" ? `${c.true_peak_dbtp} dBTP` : c.silent ? "—" : t("studio.tp_not_measured")}</td><td>{c.silent ? "—" : `${c.rms_dbfs} dBFS`}</td></tr>
          ))}</tbody>
        </table>
        {a.report?.processing?.src && Object.keys(a.report.processing.src).length > 0 && <p className="hint">{t("studio.src_applied")}</p>}
        <div><button className="btn small" disabled={act.pending} onClick={() => act.run(async () => {
          const r = await api(`/audio/render-jobs/${j.id}/download`); window.location.href = r.url; })}>{a.format === "flac" ? t("studio.download_flac") : t("studio.download")}</button></div>
        <ToApp j={j} />
      </>}
      <ErrorBox error={act.error} />
    </article>
  );
}
