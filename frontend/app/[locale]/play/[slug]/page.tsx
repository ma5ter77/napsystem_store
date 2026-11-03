"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, RequireAuth, useAction, useLoad } from "@/components/ui";
import { api, ApiError, uid } from "@/lib/api";
import { detectOutput, Playback, type OfflineState } from "@/lib/playback";
import { SpeakerTest } from "@/components/SpeakerTest";
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
  // NAP-109: streaming engine; the duration lives in state so the UI re-renders when it is known
  const audio = useRef<Playback | null>(null);
  const [dur, setDur] = useState(0);
  const [offline, setOffline] = useState<OfflineState>({ state: "idle", progress: 0 });
  const [waiting, setWaiting] = useState(false);
  const [fatal, setFatal] = useState<string | null>(null);
  const [output, setOutput] = useState<number | null>(null);
  const [playing, setPlaying] = useState<any>(null);
  useEffect(() => { setOutput(detectOutput().maxChannels); }, []);
  useEffect(() => () => { audio.current?.close(); audio.current = null; }, []);

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

  // NAP-108: headphones always get the 2.0 track; speakers / the chair get the richest version the output can play
  const maxChannels = device?.adapter === "web_headphones" ? 2 : (output ?? 2);
  async function start() {
    const s = await act.run(() => api("/sessions", { body: { app_id: app.id, session_key: key, device_id: deviceId, max_channels: maxChannels } }));
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
    const ok = await act.run(async () => {
      // a fresh short-lived link: the one from session start may have expired while the questionnaire was open
      const grant = await api<any>(`/sessions/${session.id}/grant?max_channels=${maxChannels}`, { method: "POST" });
      setPlaying(grant.playback);
      audio.current?.close();
      const pb = new Playback({
        url: grant.asset.url, channels: grant.playback?.channels ?? 2, sha256: grant.asset.sha256, sizeBytes: grant.asset.size_bytes,
        renew: async () => (await api<any>(`/sessions/${session.id}/grant?max_channels=${maxChannels}`, { method: "POST" })).asset.url,
        onEnded: () => { const d = audio.current?.duration ?? 0; finishRef.current(listenedRef.current >= d * 0.8); },  // skipping to the end is not a completed session
        onOffline: setOffline,
        onWaiting: setWaiting,
        onFatal: (code) => { audio.current?.pause(); setFatal(code); },
      });
      audio.current = pb;
      try { setDur(await pb.load()); }
      catch (e: any) { throw new ApiError(0, e?.message === "channels" ? "channels" : "download_failed", e?.message === "channels" ? t("player.fatal_channels") : t("player.download_failed")); }
      return true;
    });
    if (ok) setPhase("player");
  }, [session, act, t, maxChannels]);

  useEffect(() => {
    if (phase !== "player") return;
    const iv = setInterval(() => {
      const a = audio.current;
      if (!a) return;
      if (a.playing && !waitingRef.current && !dragRef.current) listenedRef.current += 0.25;  // NAP-042: time actually listened, not skipped
      if (!dragRef.current) setPos(Math.min(a.position, a.duration || 0));
    }, 250);
    const ping = setInterval(() => { if (audio.current?.playing) q.push("progress", { position_seconds: Math.round(posRef.current) }); }, 15000);
    return () => { clearInterval(iv); clearInterval(ping); };
  }, [phase, q]);
  const posRef = useRef(0);
  posRef.current = pos;
  const listenedRef = useRef(0);
  const dragRef = useRef(false);
  const waitingRef = useRef(false);
  waitingRef.current = waiting;

  // NAP-042: seek — the new position is queued as an event (works offline, synced later)
  function seek(to: number) {
    const a = audio.current;
    if (!a) return;
    const t2 = Math.max(0, Math.min(to, a.duration - 0.5));
    const from = a.position;
    a.seek(t2);
    setPos(t2);
    q.push("progress", { position_seconds: Math.round(t2), seek_from: Math.round(from) });
  }

  async function onPlay() {
    setFatal(null);
    // play first (inside the click, as browsers require), then report the state
    const ok = await act.run(async () => { await audio.current!.play(); await command(state === "paused" ? "resume" : "start"); return true; });
    if (!ok) audio.current?.pause();  // the server refused: do not keep playing a session it does not count
  }
  async function onPause() {
    const a = audio.current!;
    a.pause();
    const p = a.position;
    q.push("progress", { position_seconds: p });
    await command("pause", { position_seconds: p });
  }
  const finishRef = useRef<(completed: boolean) => void>(() => {});
  finishRef.current = (c) => { finish(c); };
  async function finish(completed: boolean) {
    const a = audio.current;
    a?.pause(); // local stop always works, also offline
    const final = completed ? "completed" : "cancelled";
    q.push("state", { state: final, position_seconds: Math.round(posRef.current), listened_seconds: Math.round(listenedRef.current) });
    try { await command(completed ? "complete" : "stop", { position_seconds: posRef.current, listened_seconds: Math.round(listenedRef.current) }); } catch { /* reported by event */ }
    setState(final);
    a?.close(); audio.current = null;
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
          {output !== null && <p className="hint" data-testid="browser-output">{t("player.output", { n: output })}</p>}
          {session?.grant?.playback && <p data-testid="will-play">{t("player.will_play", { layout: t(`layout.${session.grant.playback.layout}`), n: session.grant.playback.channels })}</p>}
          {session?.grant?.playback && session.grant.playback.available.length > 1 && session.grant.playback.layout === "headphones" && device?.adapter !== "web_headphones" &&
            <p className="hint">{t("player.more_layouts", { list: session.grant.playback.available.filter((l: string) => l !== "headphones").map((l: string) => t(`layout.${l}`)).join(", ") })}{" "}
              <Link href={`/${locale}/help/playback`}>{t("help.how")}</Link></p>}
          {session?.grant?.playback && session.grant.playback.channels > 2 && <SpeakerTest order={session.grant.playback.channel_order} />}
          <button className="btn" disabled={act.pending || (needsHp && (!hp || !lr))} onClick={doPrecheck}>{t("player.continue")}</button>
        </div>
      )}
      {phase === "pre" && <Questionnaire q={qPre.data} phase="pre" onDone={(b) => answer("pre", b)} />}
      {phase === "loading" && <div className="panel"><Loading /><p className="hint">{t("player.downloading")}</p></div>}
      {phase === "player" && dur > 0 && (
        <div className="panel stack">
          <div className="player-time">{duration(pos, locale)} <span className="muted" style={{ fontSize: "1rem" }}>/ {duration(dur, locale)}</span></div>
          <div className="hint" aria-live="off">{t("player.remaining", { time: duration(Math.max(0, dur - pos), locale) })}{waiting && <> · <span role="status">{t("player.buffering")}</span></>}</div>
          <input type="range" className="seek" min={0} max={Math.floor(dur)} step={1} value={Math.floor(pos)}
            aria-label={t("player.seek")} aria-valuetext={`${duration(pos, locale)} / ${duration(dur, locale)}`}
            style={{ ["--p" as string]: `${dur ? (pos / dur) * 100 : 0}%` }}
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
          {fatal && <div className="notice bad" role="alert">{t(`player.fatal_${fatal}`)}{fatal === "decode" && <> <Link href={`/${locale}/help/playback`}>{t("help.how")}</Link></>}</div>}
          <p className="hint" role="status" data-testid="offline-copy">{offline.state === "saving" ? t("player.offline_saving", { pct: Math.round(offline.progress * 100) })
            : offline.state === "idle" ? t("player.offline_saving", { pct: 0 }) : t(`player.offline_${offline.state}`)}</p>
          <p className="hint">{t("player.seek_hint")}</p>
          {playing && <p className="hint" data-testid="now-playing">{t("player.will_play", { layout: t(`layout.${playing.layout}`), n: playing.channels })}</p>}
          <p className="hint">{playing && playing.channels > 2 ? t("player.multi_note") : t("player.stereo_note")}</p>
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
