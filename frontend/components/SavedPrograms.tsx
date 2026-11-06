"use client";
// NAP-104, stage 2: saving a program for listening without the internet, the list of saved programs with a simple
// player, and the devices that keep protected copies. Copies stay encrypted on disk; see lib/offline.ts.

import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";
import { date, duration } from "@/lib/i18n";
import { NapxError } from "@/lib/napx";
import * as offline from "@/lib/offline";
import { Playback, type OfflineState } from "@/lib/playback";

function deviceName() {
  const ua = typeof navigator === "undefined" ? "" : navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} · ${os}` : browser;
}

/** Button on the player page: keep this session for listening without the internet. */
export function SaveOffline({ app, sessionKey, sessionTitle }: { app: { id: string; title: string }; sessionKey: string; sessionTitle: string }) {
  const { t, locale } = useApp();
  const act = useAction();
  const [share, setShare] = useState<number | null>(null);
  const [done, setDone] = useState<offline.SavedProgram | null>(null);
  useEffect(() => { setDone(null); }, [sessionKey]);
  if (!offline.supported()) return <p className="hint">{t("offline.unsupported")}</p>;
  async function save() {
    setShare(0);
    const r = await act.run(() => offline.save({ appId: app.id, appTitle: app.title, sessionKey, sessionTitle, deviceName: deviceName(), onProgress: setShare }));
    setShare(null);
    if (r) setDone(r);
  }
  return (
    <div className="stack" data-testid="save-offline">
      <div className="row">
        <button type="button" className="btn ghost small" disabled={act.pending || !sessionKey} onClick={save}>{t("offline.save")}</button>
        {share !== null && <span className="hint" role="status">{t("offline.saving", { pct: Math.round(share * 100) })}</span>}
        {done && <span className="hint" role="status">{t("offline.saved", { date: date(done.expires_at, locale) })}</span>}
      </div>
      <p className="hint">{t("offline.save_hint")}</p>
      <ErrorBox error={act.error} />
    </div>
  );
}

function SavedPlayer({ p, onClose }: { p: offline.SavedProgram; onClose: () => void }) {
  const { t, locale } = useApp();
  const pb = useRef<Playback | null>(null);
  const url = useRef<string | null>(null);
  const [opening, setOpening] = useState(0);
  const [ready, setReady] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let closed = false;
    (async () => {
      try {
        const blob = await offline.openForPlayback(p, setOpening);
        if (closed) return;
        url.current = URL.createObjectURL(blob);
        const a = new Playback({
          url: url.current, channels: p.playback.channels, sha256: p.package.sha256, sizeBytes: blob.size, local: true,
          renew: async () => url.current!, onEnded: () => setPlaying(false), onOffline: (_: OfflineState) => {},
          onFatal: (code) => { setErr(code === "channels" ? t("player.fatal_channels") : t("offline.err_decode")); setPlaying(false); },
        });
        pb.current = a;
        a.setVolume(0.7);
        setDur(await a.load());
        setReady(true);
      } catch (e) {
        const code = e instanceof NapxError ? e.code : "decode";
        if (!closed) setErr(t(`offline.err_${["lease_expired", "wrong_device", "tampered", "bad_signature", "bad_lease", "truncated", "missing"].includes(code) ? code : "decode"}`));
      }
    })();
    const iv = setInterval(() => { if (pb.current) setPos(pb.current.position); }, 250);
    return () => { closed = true; clearInterval(iv); pb.current?.close(); if (url.current) URL.revokeObjectURL(url.current); };
  }, [p, t]);

  async function toggle() {
    const a = pb.current!;
    if (a.playing) { await a.pause(); setPlaying(false); }
    else { await a.play(); setPlaying(true); }
  }
  return (
    <div className="panel stack" data-testid="saved-player">
      <div className="spread"><strong>{p.app.title} — {p.session.title}</strong>
        <button type="button" className="btn ghost small" onClick={onClose}>{t("offline.close")}</button></div>
      {err ? <div className="notice bad" role="alert">{err}</div> : !ready ? (
        <p className="hint" role="status">{t("offline.opening", { pct: Math.round(opening * 100) })}</p>
      ) : (
        <>
          <div className="player-time">{duration(pos, locale)} <span className="muted" style={{ fontSize: "1rem" }}>/ {duration(dur, locale)}</span></div>
          <input type="range" className="seek" min={0} max={Math.floor(dur)} step={1} value={Math.floor(pos)} aria-label={t("player.seek")}
            aria-valuetext={`${duration(pos, locale)} / ${duration(dur, locale)}`} style={{ ["--p" as string]: `${dur ? (pos / dur) * 100 : 0}%` }}
            onChange={(e) => { pb.current?.seek(Number(e.target.value)); setPos(Number(e.target.value)); }} />
          <div className="row"><button type="button" className="btn" onClick={toggle}>{playing ? t("player.pause") : t("player.play")}</button></div>
          <p className="hint">{t("offline.not_counted")}</p>
        </>
      )}
    </div>
  );
}

/** Library section: what is saved on this device, with expiry; copies no longer allowed are removed when online. */
export function SavedPrograms() {
  const { t, locale } = useApp();
  const [items, setItems] = useState<offline.SavedProgram[] | null>(null);
  const [open, setOpen] = useState<offline.SavedProgram | null>(null);
  const reload = useCallback(async () => {
    try { setItems(await offline.list()); setItems(await offline.sync()); } catch { setItems([]); }
  }, []);
  useEffect(() => {
    if (!offline.supported()) { setItems([]); return; }
    reload();
    window.addEventListener("online", reload);
    return () => window.removeEventListener("online", reload);
  }, [reload]);
  if (items === null) return <Loading />;
  return (
    <section className="stack" aria-labelledby="saved-h" data-testid="saved-programs">
      <h2 id="saved-h">{t("offline.title")}</h2>
      {!offline.supported() ? <p className="hint">{t("offline.unsupported")}</p>
        : items.length === 0 ? <p className="muted">{t("offline.empty")}</p> : (
          <div className="stack">
            {items.map((p) => (
              <div key={p.lease_id} className="panel flat spread">
                <div><strong>{p.app.title} — {p.session.title}</strong>
                  <div className="hint">{duration(p.session.duration_seconds, locale)} · {t("offline.until", { date: date(p.expires_at, locale) })}</div></div>
                <div className="row">
                  <button type="button" className="btn small" onClick={() => setOpen(p)}>{t("library.play")}</button>
                  <button type="button" className="btn ghost small" onClick={async () => { if (open?.lease_id === p.lease_id) setOpen(null); await offline.remove(p.lease_id); reload(); }}>{t("offline.remove")}</button>
                </div>
              </div>
            ))}
          </div>
        )}
      {open && <SavedPlayer key={open.lease_id} p={open} onClose={() => setOpen(null)} />}
      <p className="hint">{t("offline.honest")}</p>
    </section>
  );
}

/** Profile → Devices: browsers and apps that keep protected copies; removing one revokes all its copies. */
export function OfflineDevices() {
  const { t, locale } = useApp();
  const { data, error, loading, reload } = useLoad<any>("/me/license-devices");
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  return (
    <section className="stack" aria-labelledby="offline-dev-h">
      <h2 id="offline-dev-h">{t("offline.devices")}</h2>
      {data.items.length === 0 ? <p className="muted">{t("offline.no_devices")}</p> : data.items.map((d: any) => (
        <div key={d.id} className="panel flat spread">
          <div><strong>{d.name}</strong> {d.status === "revoked" && <span className="badge gray">{t("offline.revoked")}</span>}
            <div className="hint">{t("offline.device_meta", { date: date(d.created_at, locale), count: d.active_leases })}</div></div>
          {d.status === "active" && <button type="button" className="btn ghost small" disabled={act.pending}
            onClick={async () => { if (await act.run(() => api(`/me/license-devices/${d.id}`, { method: "DELETE" }))) reload(); }}>{t("offline.revoke")}</button>}
        </div>))}
      <p className="hint">{t("offline.devices_hint")}</p>
      <ErrorBox error={act.error} />
    </section>
  );
}
