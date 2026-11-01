"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, RequireAuth, useAction, useLoad } from "@/components/ui";
import { api, ApiError, sha256Hex, uid } from "@/lib/api";
import { duration } from "@/lib/i18n";

type Phase = "setup" | "precheck" | "pre" | "loading" | "player" | "post" | "done";

/** Buffered, ordered events. Survive a short connection loss and are sent exactly once (server dedupes). */
function useEventQueue(sessionId: string | null) {
  const seq = useRef(0);
  const key = sessionId ? `napq:${sessionId}` : "";
  const read = (): any[] => { try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch { return []; } };
  const write = (q: any[]) => { try { localStorage.setItem(key, JSON.stringify(q)); } catch { /* */ } };
  const flush = useCallback(async () => {
    if (!sessionId) return null;
    const q = read();
    if (!q.length || !navigator.onLine) return null;
    try {
      const r = await api(`/sessions/${sessionId}/events`, { body: { events: q } });
      const done = new Set([...(r.accepted ?? []), ...(r.duplicates ?? [])]);
      write(read().filter((e) => !done.has(e.event_id)));
      return r;
    } catch { return null; }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);
  const push = useCallback((type: string, data: Record<string, unknown>) => {
    if (!sessionId) return;
    const q = read();
    seq.current = Math.max(seq.current, ...q.map((e) => e.seq), 0) + 1;
    q.push({ event_id: uid(), seq: seq.current, type, occurred_at: new Date().toISOString(), data });
    write(q);
    flush();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, flush]);
  useEffect(() => {
    const on = () => flush();
    window.addEventListener("online", on);
    const iv = setInterval(flush, 10000);
    return () => { window.removeEventListener("online", on); clearInterval(iv); };
  }, [flush]);
  const pending = () => read().length;
  return { push, flush, pending, seq };
}

function Questionnaire({ q, onDone, phase }: { q: any; phase: "pre" | "post"; onDone: (r: any) => void }) {
  const { t } = useApp();
  const [answers, setAnswers] = useState<Record<string, number>>({});
  if (!q) return <Loading />;
  return (
    <form className="panel stack" onSubmit={(e) => { e.preventDefault(); onDone({ status: "completed", answers }); }}>
      <h2>{t(`player.q_${phase}`)}</h2>
      <p className="hint">{t("player.q_hint")}</p>
      {q.items.map((it: any) => (
        <label key={it.id} className="field">{it.label}: <strong>{answers[it.id] ?? "—"}</strong>
          <input type="range" min={it.min} max={it.max} step={1} value={answers[it.id] ?? Math.round((it.min + it.max) / 2)}
                 aria-valuetext={`${answers[it.id] ?? Math.round((it.min + it.max) / 2)} (${it.low} — ${it.high})`}
                 onChange={(e) => setAnswers({ ...answers, [it.id]: Number(e.target.value) })} />
          <span className="spread hint"><span>{it.low}</span><span>{it.high}</span></span>
        </label>
      ))}
      <div className="row">
        <button className="btn" disabled={q.items.some((it: any) => answers[it.id] === undefined)}>{t("player.q_save")}</button>
        <button type="button" className="btn ghost" onClick={() => onDone({ status: "skipped", answers: {} })}>{t("player.q_skip")}</button>
      </div>
    </form>
  );
}

function LrTest({ onResult }: { onResult: (ok: boolean) => void }) {
  const { t } = useApp();
  const [step, setStep] = useState<0 | 1 | 2>(0);
  const tone = (ch: 0 | 1) => {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    const merger = ctx.createChannelMerger(2);
    osc.frequency.value = 440;
    g.gain.setValueAtTime(0, ctx.currentTime);
    g.gain.linearRampToValueAtTime(0.15, ctx.currentTime + 0.05);
    g.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.9);
    osc.connect(g).connect(merger, 0, ch);
    merger.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 1);
    osc.onended = () => ctx.close();
  };
  return (
    <div className="stack">
      <div className="lr"><button className="side l btn ghost" onClick={() => { tone(0); setStep(1); }}>{t("player.lr_left")}</button>
        <button className="side r btn ghost" onClick={() => { tone(1); setStep(2); }}>{t("player.lr_right")}</button></div>
      {step === 2 && (
        <div className="row">
          <button className="btn small" onClick={() => onResult(true)}>{t("player.lr_ok")}</button>
          <button className="btn ghost small" onClick={() => onResult(false)}>{t("player.lr_bad")}</button>
        </div>
      )}
      <p className="hint">{t("player.lr_disclaimer")}</p>
    </div>
  );
}

function Player() {
  const { slug } = useParams<{ slug: string }>();
  const { t, locale } = useApp();
  const { data: app, error: appErr } = useLoad<any>(`/catalog/apps/${slug}`);
  const devices = useLoad<any>("/devices");
  // questionnaire definitions are loaded up front so the post-session form also works after a connection drop
  const qPre = useLoad<any>(app?.questionnaires?.pre ? `/questionnaires/${app.questionnaires.pre}` : null);
  const qPost = useLoad<any>(app?.questionnaires?.post ? `/questionnaires/${app.questionnaires.post}` : null);
  const [deviceId, setDeviceId] = useState<string>("");
  const [key, setKey] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("setup");
  const [session, setSession] = useState<any>(null);
  const [ver, setVerState] = useState(0);
  const verRef = useRef(0);
  const setVer = (v: number) => { verRef.current = v; setVerState(v); };
  const [lr, setLr] = useState<boolean | null>(null);
  const [hp, setHp] = useState(false);
  const [pos, setPos] = useState(0);
  const [state, setState] = useState("created");
  const [online, setOnline] = useState(true);
  const [result, setResult] = useState<any>(null);
  const act = useAction();
  const q = useEventQueue(session?.id ?? null);
  const audio = useRef<{ ctx: AudioContext; buf: AudioBuffer; src?: AudioBufferSourceNode; startedAt: number; offset: number } | null>(null);

  useEffect(() => {
    const u = () => setOnline(navigator.onLine);
    window.addEventListener("online", u); window.addEventListener("offline", u);
    return () => { window.removeEventListener("online", u); window.removeEventListener("offline", u); };
  }, []);
  useEffect(() => { if (app && !key && app.program?.[0]) setKey(app.program[0].key); }, [app, key]);
  useEffect(() => { if (devices.data?.items?.length && !deviceId) setDeviceId(devices.data.items[0].id); }, [devices.data, deviceId]);

  const device = devices.data?.items?.find((d: any) => d.id === deviceId);

  async function command(cmd: string, data: Record<string, unknown> = {}) {
    // commands carry command_id + expected state version; retries return the previous result
    try {
      const r = await api(`/sessions/${session.id}/commands`, { body: { command_id: uid(), command: cmd, expected_version: verRef.current, data } });
      setVer(r.state_version); setState(r.state);
      return r;
    } catch (e) {
      if ((e as ApiError).status === 0) { q.push("command_offline", { command: cmd }); return null; }
      throw e;
    }
  }

  async function addDevice(adapter: string, profile?: string) {
    const d = await act.run(() => api("/devices", { body: { adapter, profile } }));
    if (d) { await devices.reload(); setDeviceId(d.id); }
  }

  async function start() {
    const s = await act.run(() => api("/sessions", { body: { app_id: app.id, session_key: key, device_id: deviceId } }));
    if (!s) return;
    setSession(s); setVer(s.state_version); setState(s.state);
    setPhase("precheck");
  }

  async function doPrecheck() {
    const r = await act.run(async () => {
      await command("precheck", { headphones_confirmed: hp, lr_test_passed: !!lr });
      return command("confirm_ready");
    });
    if (r) setPhase(session.pre_status === "pending" ? "pre" : "loading");
  }

  const download = useCallback(async () => {
    setPhase("loading");
    await act.run(async () => {
      let grant = session.grant;
      let res = await fetch(grant.asset.url);
      if (res.status === 410) { grant = await api(`/sessions/${session.id}/grant`, { method: "POST" }); res = await fetch(grant.asset.url); }
      if (!res.ok) throw new ApiError(res.status, "download_failed", t("player.download_failed"));
      const raw = await res.arrayBuffer();
      if ((await sha256Hex(raw)) !== grant.asset.sha256) throw new ApiError(0, "checksum", t("player.checksum_failed"));
      const ctx = new AudioContext({ sampleRate: 48000 });
      ctx.destination.channelInterpretation = "discrete"; // no implicit downmix
      const buf = await ctx.decodeAudioData(raw);
      if (buf.numberOfChannels !== 2) throw new ApiError(0, "channels", t("player.not_stereo"));
      audio.current = { ctx, buf, startedAt: 0, offset: 0 };
      setPhase("player");
    });
  }, [session, act, t]);

  useEffect(() => {
    if (phase !== "player") return;
    const iv = setInterval(() => {
      const a = audio.current;
      if (!a) return;
      const p = a.src ? a.offset + (a.ctx.currentTime - a.startedAt) : a.offset;
      if (a.src && !dragRef.current) listenedRef.current += 0.25;  // NAP-042: time actually listened, not skipped
      if (!dragRef.current) setPos(Math.min(p, a.buf.duration));
    }, 250);
    const ping = setInterval(() => { if (audio.current?.src) q.push("progress", { position_seconds: Math.round(posRef.current) }); }, 15000);
    return () => { clearInterval(iv); clearInterval(ping); };
  }, [phase, q]);
  const posRef = useRef(0);
  posRef.current = pos;
  const listenedRef = useRef(0);
  const dragRef = useRef(false);

  // NAP-042: seek — the new position is queued as an event (works offline, synced later)
  function seek(to: number) {
    const a = audio.current;
    if (!a) return;
    const t2 = Math.max(0, Math.min(to, a.buf.duration - 0.5));
    const from = a.src ? a.offset + (a.ctx.currentTime - a.startedAt) : a.offset;
    if (a.src) { const s = a.src; a.src = undefined; s.stop(); playFrom(t2); } else { a.offset = t2; }
    setPos(t2);
    q.push("progress", { position_seconds: Math.round(t2), seek_from: Math.round(from) });
  }

  function playFrom(offset: number) {
    const a = audio.current!;
    const src = a.ctx.createBufferSource();
    src.buffer = a.buf;
    src.channelCountMode = "explicit"; src.channelCount = 2; src.channelInterpretation = "discrete";
    src.connect(a.ctx.destination);
    src.onended = () => {
      if (audio.current?.src !== src) return;
      audio.current.src = undefined;
      if (audio.current.offset + (audio.current.ctx.currentTime - audio.current.startedAt) >= audio.current.buf.duration - 0.25)
        finish(listenedRef.current >= audio.current.buf.duration * 0.8);  // skipping to the end is not a completed session
    };
    a.src = src; a.startedAt = a.ctx.currentTime; a.offset = offset;
    a.ctx.resume();
    src.start(0, offset);
  }

  async function onPlay() { await command(state === "paused" ? "resume" : "start"); playFrom(audio.current!.offset); }
  async function onPause() {
    const a = audio.current!;
    const p = a.offset + (a.ctx.currentTime - a.startedAt);
    const s = a.src; a.src = undefined; s?.stop(); a.offset = p;
    q.push("progress", { position_seconds: p });
    await command("pause", { position_seconds: p });
  }
  async function finish(completed: boolean) {
    const a = audio.current;
    if (a?.src) { const s = a.src; a.src = undefined; s.stop(); } // local stop always works, also offline
    const final = completed ? "completed" : "cancelled";
    q.push("state", { state: final, position_seconds: Math.round(posRef.current), listened_seconds: Math.round(listenedRef.current) });
    try { await command(completed ? "complete" : "stop", { position_seconds: posRef.current, listened_seconds: Math.round(listenedRef.current) }); } catch { /* reported by event */ }
    setState(final);
    a?.ctx.close();
    setPhase(session.post_status === "pending" ? "post" : "done");
  }

  async function answer(phaseName: "pre" | "post", body: any) {
    const r = await act.run(() => api(`/sessions/${session.id}/questionnaire`, { body: { phase: phaseName, ...body } }));
    if (!r) return;
    if (phaseName === "pre") download();
    else { setResult(await api(`/sessions/${session.id}`)); setPhase("done"); }
  }

  if (appErr) return <ErrorBox error={appErr} />;
  if (!app || devices.loading) return <Loading />;
  if (!app.access?.has_access) return <Empty action={<Link className="btn" href={`/${locale}/apps/${slug}`}>{t("player.to_card")}</Link>}>{t("player.no_access")}</Empty>;
  const needsHp = device?.capabilities?.channel_separation === "user_confirmation_required";

  return (
    <div className="stack" style={{ maxWidth: 760 }}>
      <h1>{app.title}</h1>
      {!online && <div className="notice warn" role="status">{t("player.offline", { count: q.pending() })}</div>}
      {phase === "setup" && (
        <div className="panel stack">
          <label className="field">{t("player.session")}
            <select value={key} onChange={(e) => setKey(e.target.value)}>
              {app.program.map((s: any) => <option key={s.key} value={s.key}>{s.title} — {duration(s.duration_seconds, locale)}</option>)}
            </select></label>
          {devices.data?.items?.length ? (
            <label className="field">{t("player.device")}
              <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
                {devices.data.items.map((d: any) => <option key={d.id} value={d.id}>{d.name}{d.simulated ? ` (${t("player.simulator")})` : ""}</option>)}
              </select></label>
          ) : <p className="muted">{t("player.no_devices")}</p>}
          <div className="row">
            <button className="btn ghost small" onClick={() => addDevice("web_headphones")}>{t("player.add_web")}</button>
            <button className="btn ghost small" onClick={() => addDevice("simulated", "sim-chair-v1")}>{t("player.add_sim")}</button>
            <button className="btn ghost small" onClick={() => addDevice("simulated", "sim-speakers")}>{t("player.add_speakers")}</button>
          </div>
          <button className="btn" disabled={!deviceId || act.pending} onClick={start}>{t("player.prepare")}</button>
        </div>
      )}
      {phase === "precheck" && (
        <div className="panel stack">
          <h2>{t("player.precheck")}</h2>
          {needsHp ? (
            <>
              <label className="check"><input type="checkbox" checked={hp} onChange={(e) => setHp(e.target.checked)} /> {t("player.hp_confirm")}</label>
              <LrTest onResult={setLr} />
              {lr === false && <div className="notice bad">{t("player.lr_failed")}</div>}
            </>
          ) : <p>{device?.simulated ? t("player.sim_precheck") : t("player.hw_precheck")}</p>}
          <button className="btn" disabled={act.pending || (needsHp && (!hp || !lr))} onClick={doPrecheck}>{t("player.continue")}</button>
        </div>
      )}
      {phase === "pre" && <Questionnaire q={qPre.data} phase="pre" onDone={(b) => answer("pre", b)} />}
      {phase === "loading" && <div className="panel"><Loading /><p className="hint">{t("player.downloading")}</p></div>}
      {phase === "player" && audio.current && (
        <div className="panel stack">
          <div className="player-time">{duration(pos, locale)} <span className="muted" style={{ fontSize: "1rem" }}>/ {duration(audio.current.buf.duration, locale)}</span></div>
          <div className="hint" aria-live="off">{t("player.remaining", { time: duration(Math.max(0, audio.current.buf.duration - pos), locale) })}</div>
          <input type="range" className="seek" min={0} max={Math.floor(audio.current.buf.duration)} step={1} value={Math.floor(pos)}
            aria-label={t("player.seek")} aria-valuetext={`${duration(pos, locale)} / ${duration(audio.current.buf.duration, locale)}`}
            style={{ ["--p" as string]: `${(pos / audio.current.buf.duration) * 100}%` }}
            onPointerDown={() => { dragRef.current = true; }}
            onChange={(e) => { setPos(Number(e.target.value)); if (!dragRef.current) seek(Number(e.target.value)); }}
            onPointerUp={(e) => { dragRef.current = false; seek(Number((e.target as HTMLInputElement).value)); }} />
          <div className="row player-controls">
            <button className="btn ghost" onClick={() => seek(pos - 30)} aria-label={t("player.back30")}>−30 {t("player.sec")}</button>
            {state === "running" ? <button className="btn" onClick={onPause}>{t("player.pause")}</button>
              : <button className="btn" onClick={onPlay}>{state === "paused" ? t("player.resume") : t("player.play")}</button>}
            <button className="btn ghost" onClick={() => seek(pos + 30)} aria-label={t("player.fwd30")}>+30 {t("player.sec")}</button>
            <button className="btn danger" onClick={() => finish(false)}>{t("player.stop")}</button>
          </div>
          <p className="hint">{t("player.seek_hint")}</p>
          <p className="hint">{t("player.stereo_note")}</p>
          <p className="hint">{t("player.tab_note")}</p>
        </div>
      )}
      {phase === "post" && <Questionnaire q={qPost.data} phase="post" onDone={(b) => answer("post", b)} />}
      {phase === "done" && (
        <div className="panel stack">
          <h2>{t(`player.end_${state}`)}</h2>
          {result && Object.keys(result.delta ?? {}).length > 0 && (
            <table><thead><tr><th>{t("player.metric")}</th><th>{t("player.before")}</th><th>{t("player.after")}</th><th>{t("player.diff")}</th></tr></thead>
              <tbody>{Object.keys(result.delta).map((k) => (
                <tr key={k}><td>{t(`metrics.${k}`)}</td><td>{result.pre[k]}</td><td>{result.post[k]}</td><td>{result.delta[k] > 0 ? "+" : ""}{result.delta[k]}</td></tr>))}</tbody></table>
          )}
          <p className="hint">{t("player.self_report_note")}</p>
          <Link className="btn ghost" href={`/${locale}/account?tab=history`}>{t("player.history")}</Link>
        </div>
      )}
      <ErrorBox error={act.error} />
    </div>
  );
}

export default function PlayPage() {
  return <div className="section"><RequireAuth><Player /></RequireAuth></div>;
}
