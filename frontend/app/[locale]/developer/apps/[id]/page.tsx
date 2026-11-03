"use client";

import { Breadcrumbs, PageHead } from "@/components/nav";
import { StatusTimeline } from "@/components/StatusTimeline";
import { conversionSteps, versionSteps } from "@/lib/steps";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Providers";
import { AppCover } from "@/components/Art";
import { Term } from "@/components/Term";
import { ErrorBox, errorFields, Loading, Price, RequireAuth, StatusBadge, useAction, useLoad } from "@/components/ui";
import { api, sha256File, uid, uploadWithProgress } from "@/lib/api";
import { date, duration } from "@/lib/i18n";

const TABS = ["details", "audio", "program", "price", "versions"] as const;
const CAPS = ["stereo_output", "stereo_separation", "vibroacoustic_output", "heart_rate_sensor", "hrv_sensor"];

function Details({ app, v, reload, done }: { app: any; v: any; reload: () => void; done: (m: string) => void }) {
  const { t } = useApp();
  const cats = useLoad<any>("/categories?kind=app");
  const [m, setM] = useState<any>(v.metadata);
  const [lang, setLang] = useState(v.content_language);
  const [caps, setCaps] = useState<string[]>(v.requirements.required_capabilities ?? []);
  const [api_v, setApiV] = useState(v.requirements.min_api_version ?? "1.0");
  const [label, setLabel] = useState(v.version_label);
  const [cat, setCat] = useState("");
  const [rights, setRights] = useState(v.rights_confirmed);
  const act = useAction();
  const editable = v.state === "draft";
  // unsaved edits are easy to lose when switching tabs: say so next to the button
  const snapshot = JSON.stringify({ m, lang, caps, api_v, label, cat, rights });
  const [initial, setInitial] = useState(snapshot);
  const dirty = editable && snapshot !== initial;
  const field = (loc: string, k: string, area = false) => {
    const val = m?.[loc]?.[k] ?? "";
    const on = (e: any) => setM({ ...m, [loc]: { ...(m?.[loc] ?? {}), [k]: e.target.value } });
    return <label className="field">{t(`dev.f_${k}`)} ({loc.toUpperCase()})
      {area ? <textarea value={val} onChange={on} disabled={!editable} lang={loc} /> : <input value={val} onChange={on} disabled={!editable} lang={loc} />}</label>;
  };
  return (
    <form className="stack" onSubmit={async (e) => {
      e.preventDefault();
      const clean: any = {};
      for (const loc of ["ru", "en"]) if (m?.[loc]?.title) clean[loc] = Object.fromEntries(Object.entries(m[loc]).filter(([, x]) => x));
      const body: any = { metadata: clean, content_language: lang, required_capabilities: caps, min_api_version: api_v, version_label: label };
      if (cat) body.category = cat;
      if (rights && !v.rights_confirmed) body.confirm_rights = true;
      if (await act.run(() => api(`/developer/apps/${app.id}/versions/${v.id}`, { method: "PATCH", body }))) { setInitial(snapshot); reload(); done(t("dev.saved_details")); }
    }}>
      {!editable && <div className="notice warn">{t("dev.frozen")}</div>}
      <div className="two">
        <div className="panel stack">{field("ru", "title")}{field("ru", "subtitle")}{field("ru", "description", true)}</div>
        <div className="panel stack">{field("en", "title")}{field("en", "subtitle")}{field("en", "description", true)}</div>
      </div>
      <p className="hint">{t("dev.translation_hint")}</p>
      <div className="panel form wide">
        <label className="field">{t("app.category")}<select value={cat} onChange={(e) => setCat(e.target.value)} disabled={!editable}><option value="">{t("dev.keep")}</option>{cats.data?.items.map((c: any) => <option key={c.slug} value={c.slug}>{c.name}</option>)}</select></label>
        <label className="field">{t("app.audio_lang")}<select value={lang} onChange={(e) => setLang(e.target.value)} disabled={!editable}><option value="none">{t("dev.lang_none")}</option><option value="ru">Русский</option><option value="en">English</option></select></label>
        <label className="field">{t("dev.version_label")}<input value={label} onChange={(e) => setLabel(e.target.value)} disabled={!editable} /></label>
        <label className="field">{t("dev.min_api")}<input value={api_v} onChange={(e) => setApiV(e.target.value)} disabled={!editable} /></label>
      </div>
      <fieldset className="panel" disabled={!editable}><legend>{t("app.compat")}</legend>
        <div className="row">{CAPS.map((c) => <label key={c} className="check"><input type="checkbox" checked={caps.includes(c)} onChange={() => setCaps(caps.includes(c) ? caps.filter((x) => x !== c) : [...caps, c])} /> {t(`caps.${c}`)}</label>)}</div>
        <p className="hint">{t("dev.caps_hint")}</p>
      </fieldset>
      <label className="check panel flat"><input type="checkbox" checked={rights} disabled={!editable || v.rights_confirmed} onChange={(e) => setRights(e.target.checked)} /> {t("dev.rights")}</label>
      <ErrorBox error={act.error} />
      {editable && <div className="row"><button className="btn" disabled={act.pending}>{t("common.save")}</button>
        {dirty && <span className="hint" role="status">{rights && !v.rights_confirmed ? t("dev.unsaved_rights") : t("dev.unsaved")}</span>}</div>}
    </form>
  );
}

// NAP-025: uploaded cover — checked and normalized by the server (PNG/JPEG/WebP, at least 800x600, 4:3 crop, metadata
// removed); moderated with the version, shown in the store after publishing. Without it the generated cover is used.
function CoverPicker({ app, v, reload, done }: { app: any; v: any; reload: () => void; done: (m: string) => void }) {
  const { t } = useApp();
  const act = useAction();
  const [progress, setProgress] = useState<number | null>(null);
  const editable = v.state === "draft";
  const pick = async (file: File) => {
    const ok = await act.run(async () => {
      const sha = await sha256File(file);
      const tk = await api<any>(`/developer/apps/${app.id}/uploads`, { body: { filename: file.name, size_bytes: file.size, sha256: sha, kind: "cover" } });
      setProgress(0);
      await uploadWithProgress(tk.upload_url, file, (p) => setProgress(p));
      return api(`/developer/apps/${app.id}/versions/${v.id}`, { method: "PATCH", body: { cover_asset_id: tk.asset_id } });
    });
    setProgress(null);
    if (ok) { reload(); done(t("dev.cover_saved")); }
  };
  const remove = async () => {
    if (await act.run(() => api(`/developer/apps/${app.id}/versions/${v.id}`, { method: "PATCH", body: { cover_asset_id: null } }))) { reload(); done(t("dev.cover_removed")); }
  };
  return (
    <section className="panel stack" aria-labelledby="cover-h">
      <h3 id="cover-h">{t("dev.cover")}</h3>
      <div className="two">
        <div className="hero-art" style={{ aspectRatio: "4/3" }}><AppCover a={{ cover_url: v.cover?.url, cover_hue: app.cover_hue, slug: app.slug, title: v.metadata?.ru?.title }} eager /></div>
        <div className="stack">
          <p className="hint">{v.cover ? t("dev.cover_uploaded") : t("dev.cover_generated")}</p>
          <p className="hint">{t("dev.cover_rules")}</p>
          {editable ? (
            <div className="row">
              <label className="btn">{v.cover ? t("dev.cover_replace") : t("dev.cover_upload")}
                <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" disabled={act.pending}
                  onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) pick(f); }} /></label>
              {v.cover && <button type="button" className="btn ghost" disabled={act.pending} onClick={remove}>{t("dev.cover_remove")}</button>}
            </div>
          ) : <p className="hint">{t("dev.cover_frozen")}</p>}
          {progress !== null && <progress max={1} value={progress} aria-label={t("dev.cover_upload")} />}
          <ErrorBox error={act.error} />
        </div>
      </div>
    </section>
  );
}

function LayoutQa({ job, detail }: { job: any; detail: any }) {
  const { t, locale } = useApp();
  const qa = detail.report;
  const stems = detail.stems ?? {};
  return (
    <div className="stack" data-testid="layout-qa">
      <div><div className="hint">{t("dev.result")}</div><audio controls preload="none" src={detail.urls.delivery} style={{ width: "100%" }} /></div>
      {job.studio && <p className="hint">{t("lay.studio_note", { project: job.studio.project_title, rev: job.studio.revision, profile: job.studio.routing_profile_name })}</p>}
      {(detail.channels?.length ?? 2) > 2 && <p className="hint">{t("lay.listen_note", { n: detail.channels?.length ?? 0 })}</p>}
      <div className="scroll-x"><table className="channel-map">
        <thead><tr><th>{t("lay.channel")}</th><th>{t("lay.source")}</th><th>{t("terms.true_peak.name")}<Term k="true_peak" /></th><th>RMS<Term k="dbfs" /></th></tr></thead>
        <tbody>{qa.channels.map((c: any) => (
          <tr key={c.channel}><th scope="row"><span className="ch-code">{c.channel}</span> {t(`ch.${c.channel}`)}</th>
            <td>{c.source === "studio" ? t("lay.from_studio") : c.uploaded ? t("lay.own_file", { name: stems[c.channel]?.name ?? "" }) : t(`lay.from_main.${c.channel}`)}</td>
            {typeof c.true_peak_dbtp === "number" ? <><td>{c.true_peak_dbtp} dBTP</td><td>{c.rms_dbfs} dBFS</td></>
              : <><td>{t("studio.silent")}</td><td>—</td></>}</tr>))}</tbody></table></div>
      <dl className="kv">
        <dt>{t("lay.loudness")}<Term k="lufs" /></dt><dd>{String(qa.levels.output_integrated_lufs)} LUFS</dd>
        <dt>{t("qa.tp")}<Term k="true_peak" /></dt><dd>{String(qa.levels.true_peak_master_dbtp)} dBTP ({t("qa.ceiling", { c: qa.levels.true_peak_ceiling_dbtp, tol: qa.levels.true_peak_tolerance_db })})</dd>
        <dt>{t("lay.gain")}<Term k="gain" /></dt><dd>{qa.levels.common_gain_db} dB</dd>
        <dt>{t("lay.check")}</dt><dd>{!qa.verification?.passed ? t("lay.check_bad") : qa.origin === "studio" ? t("lay.check_studio") : t("lay.check_ok")}</dd>
        <dt>{t("qa.duration")}</dt><dd>{duration(detail.params.output.duration_seconds, locale)} · {detail.params.output.channels} ch · 48 kHz / 24 bit</dd>
      </dl>
      <details><summary>{t("dev.advanced")}</summary>
        <div className="row"><a href={detail.urls.master}>{t("dev.dl_master")}</a><a href={detail.urls.qa_report}>{t("dev.dl_report")}</a><a href={detail.urls.params}>{t("dev.dl_params")}</a></div>
      </details>
      <p className="hint">{t("qa.disclaimer")}</p>
    </div>
  );
}

function JobCard({ job, onChange }: { job: any; onChange: () => void }) {
  const { t, locale } = useApp();
  const [detail, setDetail] = useState<any>(null);
  const act = useAction();
  useEffect(() => {
    if (!["completed", "failed", "canceled"].includes(job.state)) return;
    if (detail?.id === job.id && detail.state === job.state) return;
    api(`/developer/conversions/${job.id}`).then(setDetail).catch(() => {});
  }, [job.id, job.state, detail]);
  const running = !["completed", "failed", "canceled"].includes(job.state);
  const qa = detail?.report;
  return (
    <div className="panel flat stack">
      <div className="spread">
        <div><strong>{job.source.name}</strong> <span className="badge gray">{t(`layout.${job.layout ?? "headphones"}`)}</span> <span className="hint">· {job.studio ? t("lay.studio_short", { rev: job.studio.revision }) : `${job.profile.slug} v${job.profile.version}`} · {date(job.created_at, locale, true)}</span></div>
        <StatusBadge ns="job_state" value={job.state} />
      </div>
      <StatusTimeline value={conversionSteps(job)} ns="conversion" label={t("steps.conversion_progress")} />
      {running && <>
        <div className="progress" aria-label={t(`job_state.${job.state}`)}><i style={{ width: `${Math.round(job.stage_progress * 100)}%` }} /></div>
        <div className="row"><span className="hint">{t(`job_state.${job.state}`)} {Math.round(job.stage_progress * 100)}% · {t("dev.attempt", { n: job.attempt, max: job.max_attempts })}</span>
          <button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/developer/conversions/${job.id}/cancel`, { method: "POST" }))) onChange(); }}>{t("dev.cancel")}</button></div>
      </>}
      {job.state === "failed" && <div className="notice bad">{t(`job_errors.${job.error_code}`)}</div>}
      {["failed", "canceled"].includes(job.state) && <div><button className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/developer/conversions/${job.id}/retry`, { method: "POST" }))) onChange(); }}>{t("dev.retry")}</button></div>}
      {job.state === "completed" && detail?.urls && ((job.layout && job.layout !== "headphones") || job.studio) && <LayoutQa job={job} detail={detail} />}
      {job.state === "completed" && detail?.urls && (!job.layout || job.layout === "headphones") && !job.studio && (
        <div className="stack">
          <div className="lr">
            <div><div className="hint">{t("dev.source")}</div><audio controls preload="none" src={detail.urls.source} style={{ width: "100%" }} /></div>
            <div><div className="hint">{t("dev.result")}</div><audio controls preload="none" src={detail.urls.delivery} style={{ width: "100%" }} /></div>
          </div>
          <dl className="kv">
            <dt>{t("qa.carriers")}<Term k="carrier" /></dt><dd>{typeof qa.layer === "object" ? <>{qa.layer.left_hz} / {qa.layer.right_hz} Hz, Δ {qa.layer.beat_hz} Hz<Term k="beat" /></> : t(`qa.${qa.layer}`)}</dd>
            {typeof qa.layer === "object" && qa.layer.curve && <><dt>{t("qa.curve")}</dt><dd>{t("qa.curve_value", { points: qa.layer.curve.points.length, err: String(qa.layer.curve.max_error_hz ?? "—") })}</dd></>}
            {typeof qa.layer === "object" && <><dt>{t("qa.separation")}<Term k="separation" /></dt><dd>{String(qa.layer.opposite_suppression_db.left)} / {String(qa.layer.opposite_suppression_db.right)} dB</dd></>}
            <dt>{t("qa.lufs")}<Term k="lufs" /></dt><dd>{String(qa.levels.source_integrated_lufs)} → {String(qa.levels.output_integrated_lufs)} LUFS</dd>
            <dt>{t("qa.tp")}<Term k="true_peak" /></dt><dd>{String(qa.levels.true_peak_master_dbtp)} dBTP ({t("qa.ceiling", { c: qa.levels.true_peak_ceiling_dbtp, tol: qa.levels.true_peak_tolerance_db })})</dd>
            <dt>{t("qa.gain")}<Term k="gain" /></dt><dd>{qa.levels.common_gain_db} dB</dd>
            <dt>{t("qa.duration")}</dt><dd>{duration(detail.params.output.duration_seconds, locale)} · {detail.params.output.frames} frames · 48 kHz / 24 bit</dd>
          </dl>
          <details><summary>{t("dev.advanced")}</summary>
            <dl className="kv hint">
              {Object.entries(detail.params.profile).map(([k, val]) => <Fragment key={k}><dt>{k}</dt><dd>{String(val)}</dd></Fragment>)}
              <dt>engine</dt><dd>{detail.params.engine_version}</dd>
              <dt>input sha256</dt><dd><code>{detail.params.input.sha256.slice(0, 16)}…</code></dd>
              <dt>master sha256</dt><dd><code>{detail.params.output.master_sha256.slice(0, 16)}…</code></dd>
            </dl>
            <div className="row"><a href={detail.urls.master}>{t("dev.dl_master")}</a><a href={detail.urls.qa_report}>{t("dev.dl_report")}</a><a href={detail.urls.params}>{t("dev.dl_params")}</a></div>
          </details>
          <p className="hint">{t("qa.disclaimer")}</p>
        </div>
      )}
      <ErrorBox error={act.error} />
    </div>
  );
}

// NAP-108: versions for speakers 3.0 and the NAP chair 5.1 / 7.1. Every channel takes the author's own file or is
// derived from the main file; the binaural shift stays in the front pair.
const SPEAKER_LAYOUTS = { acoustic_3_0: ["FL", "FR", "FC"], surround_5_1: ["FL", "FR", "FC", "LFE", "SL", "SR"],
  surround_7_1: ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"] } as const;
type SpeakerLayout = keyof typeof SPEAKER_LAYOUTS;

function LayoutBuilder({ app, assets, profile, preferMain, onCreated }: { app: any; assets: any[]; profile: string; preferMain?: string; onCreated: () => void }) {
  const { t } = useApp();
  const ready = assets.filter((a: any) => a.status === "uploaded");
  const [layout, setLayout] = useState<SpeakerLayout>("surround_5_1");
  const [main, setMain] = useState("");
  const [own, setOwn] = useState<Record<string, string>>({});
  const act = useAction();
  // by default the file the headphones track was made from, so both versions match in length
  const mainId = main || (ready.some((a: any) => a.id === preferMain) ? preferMain! : ready[ready.length - 1]?.id) || "";
  const chans = SPEAKER_LAYOUTS[layout];
  const build = async () => {
    const stems = Object.fromEntries(chans.filter((c) => own[c]).map((c) => [c, own[c]]));
    if (await act.run(() => api("/developer/conversions", { body: { app_id: app.id, source_asset_id: mainId, profile_id: profile, layout, stems },
      headers: { "Idempotency-Key": uid() } }))) { setOwn({}); onCreated(); }
  };
  return (
    <section className="panel stack" aria-labelledby="layouts-h" data-testid="layout-builder">
      <h2 id="layouts-h">{t("lay.title")}</h2>
      <p className="hint">{t("lay.lead")}</p>
      {!ready.length ? <p className="muted">{t("lay.need_file")}</p> : <>
        <fieldset className="row" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="sr-only">{t("lay.layout")}</legend>
          {(Object.keys(SPEAKER_LAYOUTS) as SpeakerLayout[]).map((l) => (
            <label key={l} className="check"><input type="radio" name="layout" checked={layout === l} onChange={() => setLayout(l)} /> {t(`layout.${l}`)}</label>))}
        </fieldset>
        <label className="field">{t("lay.main")}
          <select value={mainId} onChange={(e) => setMain(e.target.value)}>{ready.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
          <span className="hint">{t("lay.main_hint")}</span></label>
        <div className="scroll-x"><table className="channel-map">
          <thead><tr><th>{t("lay.channel")}</th><th>{t("lay.source")}</th></tr></thead>
          <tbody>{chans.map((c) => (
            <tr key={c}><th scope="row"><span className="ch-code">{c}</span> {t(`ch.${c}`)}</th>
              <td><label className="sr-only" htmlFor={`own-${c}`}>{t("lay.source_for", { ch: t(`ch.${c}`) })}</label>
                <select id={`own-${c}`} value={own[c] ?? ""} onChange={(e) => setOwn({ ...own, [c]: e.target.value })}>
                  <option value="">{t(`lay.from_main.${c}`)}</option>
                  {ready.filter((a: any) => a.id !== mainId).map((a: any) => <option key={a.id} value={a.id}>{t("lay.own_file", { name: a.name })}</option>)}
                </select></td></tr>))}</tbody></table></div>
        <p className="hint">{t("lay.rules")}</p>
        <ErrorBox error={act.error} />
        <div><button type="button" className="btn" disabled={act.pending || !mainId} onClick={build}>{t("lay.build", { layout: t(`layout.${layout}`) })}</button></div>
      </>}
    </section>
  );
}

function Audio({ app, onTrackReady }: { app: any; onTrackReady?: () => void }) {
  const { t, locale } = useApp();
  const limits = useLoad<any>("/converter/limits");
  const profiles = useLoad<any>("/developer/profiles");
  const assets = useLoad<any>(`/developer/apps/${app.id}/assets`);
  const jobs = useLoad<any>(`/developer/conversions?app_id=${app.id}`);
  const [uploads, setUploads] = useState<Record<string, { p: number; err?: string; done?: boolean }>>({});
  const [profile, setProfile] = useState("");
  const [autoConvert, setAutoConvert] = useState(true);
  const act = useAction();
  const pickDefault = (items: any[] | undefined) => items?.find((p: any) => p.slug === "alpha-demo-v1")?.id ?? items?.[0]?.id ?? "";
  useEffect(() => { if (profiles.data && !profile) setProfile(pickDefault(profiles.data.items)); }, [profiles.data, profile]);
  // the upload may finish before or after profiles load: read the latest choice when it ends
  const profileRef = useRef("");
  profileRef.current = profile || pickDefault(profiles.data?.items);
  // when a conversion finishes the server may have filled the program: refresh the version shown in other tabs
  const completedRef = useRef<number | null>(null);
  useEffect(() => {
    const n = (jobs.data?.items ?? []).filter((j: any) => j.state === "completed").length;
    if (completedRef.current !== null && n > completedRef.current) onTrackReady?.();
    if (jobs.data) completedRef.current = n;
  }, [jobs.data, onTrackReady]);
  useEffect(() => {
    const active = jobs.data?.items?.some((j: any) => !["completed", "failed", "canceled"].includes(j.state));
    if (!active) return;
    const iv = setInterval(jobs.reload, 1500); // polling; state is durable in PostgreSQL
    return () => clearInterval(iv);
  }, [jobs.data, jobs.reload]);

  async function onFiles(files: FileList | null) {
    if (!files) return;
    await Promise.all(Array.from(files).map(async (file) => {
      setUploads((u) => ({ ...u, [file.name]: { p: 0 } }));
      try {
        const sha = await sha256File(file);
        const tk = await api(`/developer/apps/${app.id}/uploads`, { body: { filename: file.name, size_bytes: file.size, sha256: sha } });
        const res = await uploadWithProgress(tk.upload_url, file, (p) => setUploads((u) => ({ ...u, [file.name]: { p } })));
        setUploads((u) => ({ ...u, [file.name]: { p: 1, done: true } }));
        // NAP-069: start the conversion right away with the chosen profile, so the next step is not missed
        if (autoConvert && res?.status === "uploaded") {
          const pid = profileRef.current || pickDefault((await api<any>("/developer/profiles")).items);
          await act.run(() => api("/developer/conversions", { body: { app_id: app.id, source_asset_id: res.asset_id ?? tk.asset_id, profile_id: pid }, headers: { "Idempotency-Key": uid() } }));
          jobs.reload();
        }
      } catch (e: any) {
        setUploads((u) => ({ ...u, [file.name]: { p: 0, err: e.message } }));
      }
    }));
    assets.reload();
  }

  const lim = limits.data;
  const prof = profiles.data?.items.find((p: any) => p.id === profile);
  return (
    <div className="stack">
      <div className="panel stack">
        <h2>{t("dev.step_profile")}</h2>
        <label className="field">{t("dev.profile")}
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>{profiles.data?.items.map((p: any) => <option key={p.id} value={p.id}>{p.name} · v{p.version}</option>)}</select></label>
        {prof && <p className="hint">{prof.description}</p>}
        {prof && <details><summary>{t("dev.advanced")}</summary><dl className="kv hint">{Object.entries(prof.params).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl><p className="hint">{t("dev.profile_locked")}</p></details>}
      </div>
      <div className="panel stack">
        <h2>{t("dev.step_upload")}</h2>
        {lim && <p className="hint">{t("dev.limits", { gb: (lim.max_upload_bytes / 1024 ** 3).toFixed(0), min: lim.max_duration_seconds / 60, formats: lim.accepted.join(", ").toUpperCase() })}</p>}
        <label className="row hint"><input type="checkbox" checked={autoConvert} onChange={(e) => setAutoConvert(e.target.checked)} /> {t("dev.auto_convert")}</label>
        <input type="file" multiple accept=".wav,.flac,.mp3,.m4a,audio/*" onChange={(e) => onFiles(e.target.files)} aria-label={t("dev.upload")} />
        {Object.entries(uploads).map(([n, u]) => (
          <div key={n}><div className="spread hint"><span>{n}</span><span>{u.err ? <span className="err-text">{u.err}</span> : u.done ? t("dev.uploaded") : `${Math.round(u.p * 100)}%`}</span></div>
            <div className="progress"><i style={{ width: `${u.p * 100}%` }} /></div></div>
        ))}
      </div>
      <div className="panel stack">
        <h2>{t("dev.step_files")}</h2>
        <p className="hint">{t("dev.files_hint")}</p>
        {assets.data?.items?.length ? (
          <table><thead><tr><th>{t("dev.file")}</th><th>{t("dev.format")}</th><th>{t("account.status")}</th><th></th></tr></thead>
            <tbody>{assets.data.items.map((a: any) => (
              <tr key={a.id}><td>{a.name}</td><td className="hint">{a.probe?.format_name ? `${a.probe.codec}, ${a.probe.channels} ch, ${a.probe.sample_rate} Hz, ${duration(a.probe.duration, locale)}` : (a.probe?.error ? t(`job_errors.${a.probe.error}`) : "—")}</td>
                <td><StatusBadge ns="asset_status" value={a.status} /></td>
                <td>{a.status === "uploaded" && <button className="btn small" disabled={act.pending} onClick={async () => {
                  if (await act.run(() => api("/developer/conversions", { body: { app_id: app.id, source_asset_id: a.id, profile_id: profile }, headers: { "Idempotency-Key": uid() } }))) jobs.reload();
                }}>{t("dev.start_conversion")}</button>}</td></tr>))}</tbody></table>
        ) : <p className="muted">{t("dev.no_assets")}</p>}
        <ErrorBox error={act.error} />
      </div>
      {profile && <LayoutBuilder app={app} assets={assets.data?.items ?? []} profile={profile} onCreated={jobs.reload}
        preferMain={(jobs.data?.items ?? []).find((j: any) => j.state === "completed" && (j.layout ?? "headphones") === "headphones")?.source?.id} />}
      <div className="stack">
        <h2>{t("dev.jobs")}</h2>
        <p className="hint">{t("dev.jobs_hint")}</p>
        {jobs.error && <ErrorBox error={jobs.error} onRetry={jobs.reload} />}
        {jobs.data?.items?.length ? jobs.data.items.map((j: any) => <JobCard key={j.id} job={j} onChange={jobs.reload} />) : <p className="muted">{t("dev.no_jobs")}</p>}
      </div>
    </div>
  );
}

function Program({ app, v, reload, saved }: { app: any; v: any; reload: () => void; saved: (m: string) => void }) {
  const { t, locale } = useApp();
  const jobs = useLoad<any>(`/developer/conversions?app_id=${app.id}`);
  const [rows, setRows] = useState<any[]>(v.sessions.map((s: any) => ({ key: s.key, ru: s.titles.ru ?? "", en: s.titles.en ?? "", job: s.conversion_job_id, variants: s.variants ?? {} })));
  const [missing, setMissing] = useState<Set<number>>(new Set());
  const act = useAction();
  const finished = (jobs.data?.items ?? []).filter((j: any) => j.state === "completed");
  const done = finished.filter((j: any) => (j.layout ?? "headphones") === "headphones");
  const editable = v.state === "draft";
  const move = (i: number, d: number) => { const r = [...rows]; [r[i], r[i + d]] = [r[i + d], r[i]]; setRows(r); setMissing(new Set()); };
  const set = (i: number, patch: any) => { setRows(rows.map((x, j) => j === i ? { ...x, ...patch } : x)); if (patch.job) setMissing(new Set([...missing].filter((m) => m !== i))); };
  // server names fields as sessions.<position>.<key|titles|track>, position starting at 1
  const bad = new Set(errorFields(act.error));
  const isBad = (i: number, f: string) => bad.has(`sessions.${i + 1}.${f}`) || (f === "track" && missing.has(i));
  const audioTab = `/${locale}/developer/apps/${app.id}?tab=audio`;
  return (
    <div className="stack">
      {!editable && <div className="notice warn">{t("dev.frozen")}</div>}
      <p className="hint">{t("dev.program_hint")}</p>
      {editable && jobs.data && done.length === 0 && (
        <div className="notice warn">{t("dev.no_tracks")} <Link href={audioTab}>{t("dev.go_audio")}</Link></div>
      )}
      {rows.map((r, i) => (
        <fieldset key={i} className="panel flat form wide" disabled={!editable}>
          <legend>{t("dev.session_n", { n: i + 1 })}</legend>
          <label className="field">{t("dev.key")}<input value={r.key} aria-invalid={isBad(i, "key") || undefined} onChange={(e) => set(i, { key: e.target.value })} /></label>
          <label className="field">{t("dev.title_ru")}<input value={r.ru} aria-invalid={isBad(i, "titles") || undefined} onChange={(e) => set(i, { ru: e.target.value })} /></label>
          <label className="field">{t("dev.title_en")}<input value={r.en} aria-invalid={isBad(i, "titles") || undefined} onChange={(e) => set(i, { en: e.target.value })} /></label>
          <label className="field">{t("dev.track")}<select required value={r.job ?? ""} aria-invalid={isBad(i, "track") || undefined} onChange={(e) => set(i, { job: e.target.value })}>
            <option value="">{t("dev.choose_track")}</option>{done.map((j: any) => <option key={j.id} value={j.id}>{j.source.name} · {j.profile.slug}</option>)}</select>
            {isBad(i, "track") && <span className="err-text">{t("dev.track_required")}</span>}</label>
          {(Object.keys(SPEAKER_LAYOUTS) as SpeakerLayout[]).map((l) => {
            const opts = finished.filter((j: any) => j.layout === l);
            if (!opts.length && !r.variants?.[l]) return null;
            return <label key={l} className="field">{t("lay.version_for", { layout: t(`layout.${l}`) })}
              <select value={r.variants?.[l] ?? ""} aria-invalid={bad.has(`sessions.${i + 1}.variants.${l}`) || undefined}
                onChange={(e) => set(i, { variants: { ...(r.variants ?? {}), [l]: e.target.value } })}>
                <option value="">{t("lay.none")}</option>{opts.map((j: any) => <option key={j.id} value={j.id}>{j.source.name} · {j.profile.slug}</option>)}</select></label>;
          })}
          <div className="row">
            <button type="button" className="btn ghost small" disabled={i === 0} onClick={() => move(i, -1)} aria-label={t("dev.up")}>↑</button>
            <button type="button" className="btn ghost small" disabled={i === rows.length - 1} onClick={() => move(i, 1)} aria-label={t("dev.down")}>↓</button>
            <button type="button" className="btn ghost small" onClick={() => { setRows(rows.filter((_, j) => j !== i)); setMissing(new Set()); }}>{t("dev.remove")}</button>
          </div>
        </fieldset>
      ))}
      {editable && <div className="row">
        <button className="btn ghost" onClick={() => setRows([...rows, { key: `s${rows.length + 1}`, ru: "", en: "", job: done[0]?.id ?? "" }])}>{t("dev.add_session")}</button>
        <button className="btn" disabled={act.pending} onClick={async () => {
          const empty = new Set(rows.flatMap((r, i) => (r.job ? [] : [i])));
          setMissing(empty);
          if (empty.size) return; // NAP-068: do not send sessions without a track
          const sessions = rows.map((r) => ({ key: r.key, titles: Object.fromEntries([["ru", r.ru], ["en", r.en]].filter(([, x]) => x)), conversion_job_id: r.job,
            variants: Object.fromEntries(Object.entries(r.variants ?? {}).filter(([, x]) => x)) }));
          if (await act.run(() => api(`/developer/apps/${app.id}/versions/${v.id}/program`, { method: "PUT", body: { sessions } }))) { reload(); saved(t("dev.saved_program")); }
        }}>{t("dev.save_program")}</button>
      </div>}
      {missing.size > 0 && <div className="notice bad" role="alert">{t("dev.tracks_missing", { list: [...missing].map((i) => i + 1).join(", ") })}</div>}
      <ErrorBox error={act.error} />
    </div>
  );
}

function PriceTab({ app, reload, done }: { app: any; reload: () => void; done: (m: string) => void }) {
  const { t } = useApp();
  const cur = app.prices[0];
  const [kind, setKind] = useState(cur?.kind ?? "one_time");
  const [amount, setAmount] = useState(cur ? cur.amount_minor / 100 : 4.99);
  const [currency, setCurrency] = useState(cur?.currency ?? "EUR");
  const [interval, setInterval] = useState(cur?.interval ?? "month");
  const act = useAction();
  return (
    <form className="panel stack" onSubmit={async (e) => {
      e.preventDefault();
      if (await act.run(() => api(`/developer/apps/${app.id}/price`, { method: "PUT", body: { kind, amount_minor: Math.round(amount * 100), currency, interval: kind === "subscription" ? interval : null } }))) { const before = app.versions[0]?.state; const fresh = await api<any>(`/developer/apps/${app.id}`).catch(() => null); reload(); done(before === "approved" && fresh?.versions?.[0]?.state === "published" ? t("dev.saved_price_published") : t("dev.saved_price")); }
    }}>
      {cur && <p>{t("dev.current_price")}: {app.prices.map((p: any, i: number) => <span key={p.currency + p.kind}>{i > 0 && " · "}<Price minor={p.amount_minor} currency={p.currency} /> ({t(`kinds.${p.kind}`)}{p.interval ? `, ${t(`interval.${p.interval}`)}` : ""})</span>)}</p>}
      <div className="form wide">
        <label className="field">{t("dev.price_kind")}<select value={kind} onChange={(e) => setKind(e.target.value)}><option value="one_time">{t("kinds.one_time")}</option><option value="subscription">{t("kinds.subscription")}</option><option value="free">{t("app.free")}</option></select></label>
        {kind !== "free" && <label className="field">{t("account.amount")}<input type="number" step="0.01" min="0.5" value={amount} onChange={(e) => setAmount(Number(e.target.value))} /></label>}
        {kind !== "free" && <label className="field">{t("dev.currency")}<select value={currency} onChange={(e) => { setCurrency(e.target.value); const same = app.prices.find((p: any) => p.currency === e.target.value && p.kind === kind); if (same) setAmount(same.amount_minor / 100); }}><option>EUR</option><option>USD</option></select></label>}
        {kind === "subscription" && <label className="field">{t("dev.interval")}<select value={interval} onChange={(e) => setInterval(e.target.value)}><option value="month">{t("interval.month")}</option><option value="year">{t("interval.year")}</option></select></label>}
      </div>
      <p className="hint">{t("dev.price_note")}</p>
      <p className="hint">{t("dev.price_currencies")}</p>
      <ErrorBox error={act.error} />
      <div><button className="btn">{t("common.save")}</button></div>
    </form>
  );
}

function Versions({ app, reload, done, ready }: { app: any; reload: () => void; done: (m: string) => void; ready: boolean }) {
  const { t, locale } = useApp();
  const act = useAction();
  const latest = app.versions[0];
  const jobs = useLoad<any>(`/developer/conversions?app_id=${app.id}`);
  const go = async (path: string, msg: string, body?: any) => { if (await act.run(() => api(path, { method: body ? "PUT" : "POST", body }))) { reload(); done(msg); } };
  return (
    <div className="stack">
      <div className="row">
        {latest?.state === "draft" && <button className="btn" disabled={act.pending || !ready} onClick={() => go(`/developer/apps/${app.id}/versions/${latest.id}/submit`, t("dev.done_submitted"))}>{t("dev.submit")}</button>}
        {latest?.state === "approved" && <button className="btn" disabled={act.pending} onClick={() => go(`/developer/apps/${app.id}/versions/${latest.id}/publish`, t("dev.done_published"))}>{t("dev.publish")}</button>}
        {["submitted", "approved"].includes(latest?.state) && <button className="btn ghost" disabled={act.pending} onClick={() => go(`/developer/apps/${app.id}/versions/${latest.id}/withdraw`, t("dev.done_withdrawn"))}>{t("dev.withdraw_version")}</button>}
        {!["draft", "submitted"].includes(latest?.state) && <button className="btn ghost" disabled={act.pending} onClick={() => go(`/developer/apps/${app.id}/versions`, t("dev.done_new_version"))}>{t("dev.new_version")}</button>}
        {app.published_version_id && (app.sales_status === "active"
          ? <button className="btn ghost" onClick={() => go(`/developer/apps/${app.id}/sales`, t("dev.done_sales_stopped"), { status: "withdrawn" })}>{t("dev.stop_sales")}</button>
          : <button className="btn ghost" onClick={() => go(`/developer/apps/${app.id}/sales`, t("dev.done_sales_resumed"), { status: "active" })}>{t("dev.resume_sales")}</button>)}
      </div>
      {app.sales_status === "withdrawn" && <div className="notice warn">{t("dev.sales_withdrawn")}</div>}
      {["draft", "submitted", "approved"].includes(latest?.state) && (
        <label className="check panel flat">
          <input type="checkbox" checked={!!latest.auto_release} disabled={act.pending}
            onChange={(e) => go(`/developer/apps/${app.id}/versions/${latest.id}/auto-release`, e.target.checked ? t("dev.auto_release_on") : t("dev.auto_release_off"), { on: e.target.checked })} />
          <span>{t("dev.auto_release")}<span className="hint" style={{ display: "block" }}>{t("dev.auto_release_hint")}</span></span>
        </label>)}
      <ErrorBox error={act.error} />
      <div className="panel flat stack">
        <h3>{t("dev.public_preview")}</h3>
        <p className="hint">{t("dev.preview_hint")}</p>
        <div className="row">{(jobs.data?.items ?? []).filter((j: any) => j.state === "completed").slice(0, 5).map((j: any) => (
          <button key={j.id} className="btn ghost small" onClick={async () => { if (await act.run(() => api(`/developer/apps/${app.id}/public-preview`, { body: { conversion_job_id: j.id } }))) { reload(); done(t("dev.done_preview")); } }}>{j.source.name}</button>))}</div>
        {app.public_preview && <span className="badge ok">{t("dev.preview_published")}</span>}
      </div>
      {app.versions.map((v: any) => (
        <div key={v.id} className="panel flat stack">
          <div className="spread"><strong>v{v.version_label} (#{v.number})</strong><StatusBadge ns="version_state" value={v.state} /></div>
          <div className="hint">{t("dev.sessions_count", { count: v.sessions.length })} · {t("dev.rights_state", { ok: v.rights_confirmed ? t("common.yes") : t("common.no") })}{v.manifest_sha256 ? ` · manifest ${v.manifest_sha256.slice(0, 12)}…` : ""}</div>
          {v.moderation.map((d: any, i: number) => <div key={i} className="notice"><StatusBadge ns="decision" value={d.decision} /> {d.comment} <span className="hint">{date(d.at, locale, true)}</span></div>)}
        </div>
      ))}
    </div>
  );
}

function Checklist({ checks, goTab, editable }: { checks: any[]; goTab: (x: string) => void; editable: boolean }) {
  const { t } = useApp();
  if (!editable) return null;
  return (
    <div className="panel flat stack">
      <h3>{t("dev.check_title")}</h3>
      <ul className="checklist">{checks.map((c) => (
        <li key={c.key} className={c.ok ? "ok" : c.key === "price" ? "warn" : "bad"}>
          <span aria-hidden="true">{c.ok ? "✓" : c.key === "price" ? "!" : "✗"}</span>
          <span>{t(`dev.check.${c.key}`)}{!c.ok && c.key === "price" ? ` — ${t("dev.check_price_later")}` : ""}</span>
          {!c.ok && <button type="button" className="btn ghost small" onClick={() => goTab(c.tab)}>{t("dev.fix")}</button>}
        </li>))}</ul>
    </div>
  );
}

function NextStep({ v, app, ready, checks, decision, goTab }: { v: any; app: any; ready: boolean; checks: any[]; decision: any; goTab: (x: string) => void }) {
  const { t, locale } = useApp();
  const missing = checks.filter((c) => !c.ok && c.key !== "price");
  let cls = "notice", body: React.ReactNode = null;
  if (v.state === "draft" && !ready) body = <>{t("dev.next_fill")} {missing.map((c, i) => <span key={c.key}>{i ? ", " : ""}<button type="button" className="linklike" onClick={() => goTab(c.tab)}>{t(`dev.todo.${c.key}`)}</button></span>)}</>;
  else if (v.state === "draft") body = <>{t("dev.next_submit")} <button type="button" className="btn small" onClick={() => goTab("versions")}>{t("dev.go_submit")}</button></>;
  else if (v.state === "submitted") { cls = "notice warn"; body = t("dev.next_wait", { date: date(v.submitted_at, locale, true) }); }
  else if (v.state === "approved" && (v.release_blockers ?? []).includes("price")) { cls = "notice ok"; body = <>{v.auto_release ? t("dev.next_approved_price_auto") : `${t("dev.next_publish")} ${t("dev.next_publish_price")}`} <button type="button" className="btn small" onClick={() => goTab("price")}>{t("dev.tab_price")}</button></>; }
  else if (v.state === "approved") { cls = "notice ok"; body = <>{t("dev.next_publish")} <button type="button" className="btn small" onClick={() => goTab("versions")}>{t("dev.go_publish")}</button></>; }
  else if (v.state === "rejected") { cls = "notice bad"; body = <>{t("dev.next_rejected")}{decision?.comment ? <> «{decision.comment}»</> : null}. {t("dev.next_rejected_fix")}</>; }
  else if (v.state === "published") { cls = "notice ok"; body = <>{t("dev.next_published")} <Link href={`/${locale}/apps/${app.slug}`}>{t("dev.open_in_store")}</Link></>; }
  if (!body) return null;
  return <div className={cls} role="status"><strong>{t("dev.next_step")}:</strong> {body}</div>;
}

function Editor() {
  const { id } = useParams<{ id: string }>();
  const { t, locale } = useApp();
  const router = useRouter();
  const tab = (useSearchParams()?.get("tab") as (typeof TABS)[number]) || "details";
  const { data: app, error, loading, reload } = useLoad<any>(`/developer/apps/${id}`);
  const [flash, setFlash] = useState<string | null>(null);
  useEffect(() => { if (!flash) return; const x = setTimeout(() => setFlash(null), 6000); return () => clearTimeout(x); }, [flash]);
  const showFlash = (m: string) => setFlash(m);
  if (loading && !app) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const v = app.versions[0];
  // NAP-082: what is ready, what is missing, and what to do next — visible on every tab
  const checks = [
    { key: "title", ok: !!(v.metadata?.ru?.title || v.metadata?.en?.title), tab: "details" },
    { key: "rights", ok: !!v.rights_confirmed, tab: "details" },
    { key: "program", ok: v.sessions.length > 0, tab: v.sessions.length ? "program" : "audio" },
    { key: "price", ok: (app.prices ?? []).length > 0, tab: "price" },
  ];
  const ready = checks.filter((c) => c.key !== "price").every((c) => c.ok);
  const goTab = (x: string) => { router.replace(`/${locale}/developer/apps/${id}?tab=${x}`); reload(); };
  const lastDecision = v.moderation?.length ? v.moderation[v.moderation.length - 1] : null;
  return (
    <div className="stack">
      <div className="spread">
        <div><Breadcrumbs label={t("crumbs.label")} items={[{ href: `/${locale}`, label: t("crumbs.home") }, { href: `/${locale}/developer`, label: t("dev.title") }, { label: v.metadata?.[locale]?.title ?? v.metadata?.ru?.title ?? v.metadata?.en?.title ?? app.slug }]} /><h1>{v.metadata?.[locale]?.title ?? v.metadata?.ru?.title ?? v.metadata?.en?.title ?? app.slug}</h1></div>
        <div className="row"><StatusBadge ns="version_state" value={v.state} />{app.published_version_id && <Link href={`/${locale}/apps/${app.slug}`}>{t("dev.open_in_store")}</Link>}</div>
      </div>
      <StatusTimeline value={versionSteps(v, v.auto_release)} ns="version" label={t("steps.version_progress")} showNext={false} />
      <NextStep v={v} app={app} ready={ready} checks={checks} decision={lastDecision} goTab={goTab} />
      <div className="tabs" role="tablist">{TABS.map((x) => <button key={x} role="tab" aria-selected={tab === x} onClick={() => goTab(x)}>{t(`dev.tab_${x}`)}</button>)}</div>
      {flash && <div className="notice ok" role="status">{flash}</div>}
      {tab === "details" && <><Details key={v.id + v.state} app={app} v={v} reload={reload} done={showFlash} /><CoverPicker app={app} v={v} reload={reload} done={showFlash} /></>}
      {tab === "audio" && <Audio app={app} onTrackReady={reload} />}
      {tab === "program" && <Program key={v.id + v.sessions.length} app={app} v={v} reload={reload} saved={showFlash} />}
      {tab === "price" && <PriceTab app={app} reload={reload} done={showFlash} />}
      {tab === "versions" && <><Checklist checks={checks} goTab={goTab} editable={v.state === "draft"} /><Versions app={app} reload={reload} done={showFlash} ready={ready} /></>}
    </div>
  );
}

export default function EditorPage() {
  return <div className="section"><RequireAuth role="developer"><Suspense><Editor /></Suspense></RequireAuth></div>;
}
