"use client";

// NAP Audio Studio editor (spec v2.1 M1): bus tracks, clips with waveforms, generators, inspector, zoom/snap,
// undo/redo, autosave with optimistic revisions, server preview (same render as export) and verified exports.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import {
  addBinauralPair, addClip, addIsochronic, Bus, Clip, durationFrames, findClip, gridFrames, historyInit, historyPush,
  moveClipToTrack, redo, removeClip, snap, splitClip, State, timecode, undo, updateClip, updateGenerator, updateTrack,
  type History,
} from "./model";
import { RenderPanel, type Profile } from "./Render";
import { SourceLibrary, type SourceAsset } from "./Sources";
import { Waveform } from "./Waveform";

export type Project = { id: string; title: string; sample_rate: number; bit_depth: number; bus_count: number; revision: number;
  duration_frames: number; state: State };

type Save = { kind: "saved" | "saving" | "dirty" | "conflict" | "invalid" | "error"; error?: unknown };

const BUS_TONE: Record<Bus, string> = { NEURO_LEFT: "left", NEURO_RIGHT: "right", VOICE_CENTER: "voice", MUSIC_LEFT: "music",
  MUSIC_RIGHT: "music", LFE: "lfe", BACK_LEFT: "back", BACK_RIGHT: "back" };
const MIN_ZOOM = 2, MAX_ZOOM = 2000;

function isTyping(e: KeyboardEvent) {
  const el = e.target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

export function Editor({ project, profiles, onReload }: { project: Project; profiles: Profile[]; onReload: () => void }) {
  const { t } = useApp();
  const fs = project.sample_rate;
  const [hist, setHist] = useState<History>(() => historyInit(project.state));
  const state = hist.present;
  const [sel, setSel] = useState<{ clip?: string; track?: string }>({ track: project.state.tracks[0]?.id });
  const [zoom, setZoom] = useState(40);              // px per second
  const [snapOn, setSnapOn] = useState(true);
  const [playhead, setPlayhead] = useState(0);       // frames
  const [save, setSave] = useState<Save>({ kind: "saved" });
  const [title, setTitle] = useState(project.title);
  const [assets, setAssets] = useState<Record<string, SourceAsset>>({});
  const rev = useRef(project.revision);
  const saved = useRef<State>(project.state);
  const savedTitle = useRef(project.title);

  const grid = snapOn ? gridFrames(zoom, fs) : 0;
  const total = durationFrames(state);
  const timelineFrames = Math.max(total + 30 * fs, 120 * fs);
  const pxPerFrame = zoom / fs;

  const commit = useCallback((next: State, coalesce = false) => setHist((h) => historyPush(h, next, coalesce)), []);

  // ---------------------------------------------------------------- autosave (optimistic, base_revision)
  useEffect(() => {
    if (save.kind === "conflict") return;
    if (state === saved.current && title === savedTitle.current) return;
    setSave({ kind: "dirty" });
    const timer = setTimeout(async () => {
      setSave({ kind: "saving" });
      const sending = state, sendingTitle = title;
      try {
        const r = await api(`/audio/projects/${project.id}`, { method: "PATCH", body: { base_revision: rev.current, state: sending,
          ...(sendingTitle !== savedTitle.current ? { title: sendingTitle } : {}) } });
        rev.current = r.revision; saved.current = sending; savedTitle.current = sendingTitle;
        setSave({ kind: "saved" });
      } catch (e) {
        if (e instanceof ApiError && e.code === "revision_conflict") setSave({ kind: "conflict", error: e });
        else if (e instanceof ApiError && e.status === 422) setSave({ kind: "invalid", error: e });
        else setSave({ kind: "error", error: e });
      }
    }, 700);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, title]);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (state !== saved.current) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [state]);

  // ---------------------------------------------------------------- source metadata for clips
  useEffect(() => {
    api<{ items: SourceAsset[] }>("/audio/assets").then((r) => setAssets(Object.fromEntries(r.items.map((a) => [a.id, a])))).catch(() => {});
  }, []);

  const selected = sel.clip ? findClip(state, sel.clip) : null;
  const selTrack = state.tracks.find((x) => x.id === (selected?.track.id ?? sel.track)) ?? state.tracks[0];

  // ---------------------------------------------------------------- preview (server render, one AudioContext)
  const preview = usePreview(project.id, fs);

  // ---------------------------------------------------------------- keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e)) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") { e.preventDefault(); setHist((h) => (e.shiftKey ? redo(h) : undo(h))); }
      else if (mod && e.key.toLowerCase() === "y") { e.preventDefault(); setHist(redo); }
      else if ((e.key === "Delete" || e.key === "Backspace") && sel.clip) { e.preventDefault(); commit(removeClip(state, sel.clip)); setSel({ track: sel.track }); }
      else if (e.key.toLowerCase() === "s" && !mod && sel.clip) { e.preventDefault(); commit(splitClip(state, sel.clip, playhead)); }
      else if (e.key === " ") { e.preventDefault(); preview.playing ? preview.stop() : preview.play(playhead, total, setPlayhead); }
      else if (e.key === "+" || e.key === "=") setZoom((z) => Math.min(MAX_ZOOM, z * 1.5));
      else if (e.key === "-") setZoom((z) => Math.max(MIN_ZOOM, z / 1.5));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, sel, playhead, total, commit, preview]);

  // ---------------------------------------------------------------- clip dragging (move / trim)
  const lanes = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: string; mode: "move" | "trim-end" | "trim-start"; x0: number; clip: Clip; base: History } | null>(null);

  const onClipPointerDown = (e: React.PointerEvent, clip: Clip, trackId: string, mode: "move" | "trim-end" | "trim-start") => {
    if (e.button !== 0) return;
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setSel({ clip: clip.id, track: trackId });
    drag.current = { id: clip.id, mode, x0: e.clientX, clip, base: hist };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const df = (e.clientX - d.x0) / pxPerFrame;
    const c = d.clip;
    let next = d.base.present;
    if (d.mode === "move") {
      next = updateClip(next, c.id, { start_frame: snap(c.start_frame + df, grid) });
      const lane = (document.elementsFromPoint(e.clientX, e.clientY).find((el) => (el as HTMLElement).dataset?.lane) as HTMLElement | undefined)?.dataset.lane;
      if (lane) next = moveClipToTrack(next, c.id, lane);
    } else if (d.mode === "trim-end") {
      const maxLen = c.asset_id ? sourceFrames(assets[c.asset_id], fs) - (c.source_offset_frame ?? 0) : Infinity;
      const end = snap(c.start_frame + c.duration_frames + df, grid);
      next = updateClip(next, c.id, { duration_frames: Math.max(1, Math.min(maxLen, end - c.start_frame)) });
    } else {
      const end = c.start_frame + c.duration_frames;
      const minStart = Math.max(0, c.start_frame - (c.source_offset_frame ?? 0));
      const start = Math.min(end - 1, Math.max(minStart, snap(c.start_frame + df, grid)));
      next = updateClip(next, c.id, { start_frame: start, duration_frames: end - start,
        source_offset_frame: (c.source_offset_frame ?? 0) + (start - c.start_frame) });
    }
    setHist(historyPush(d.base, next));
  };
  const onPointerUp = () => { drag.current = null; };

  const seek = (e: React.MouseEvent) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setPlayhead(snap((e.clientX - rect.left) / pxPerFrame, 0));
  };

  // ---------------------------------------------------------------- placing material
  const placeAsset = (a: SourceAsset) => {
    const len = sourceFrames(a, fs);
    if (!selTrack || len <= 0) return;
    const [next, id] = addClip(state, selTrack.id, { start_frame: playhead, duration_frames: len, asset_id: a.id, source_channel: 0 });
    setAssets((m) => ({ ...m, [a.id]: a }));
    commit(next); setSel({ clip: id, track: selTrack.id });
  };
  const hasNeuroPair = state.tracks.some((x) => x.bus === "NEURO_LEFT") && state.tracks.some((x) => x.bus === "NEURO_RIGHT");

  const ticks = useMemo(() => {
    const step = gridFrames(zoom, fs), out: number[] = [];
    for (let f = 0; f <= timelineFrames; f += step) out.push(f);
    return out;
  }, [zoom, fs, timelineFrames]);

  return (
    <div className="studio" onPointerMove={onPointerMove} onPointerUp={onPointerUp}>
      <div className="studio-bar" role="toolbar" aria-label={t("studio.toolbar")}>
        <input className="studio-title" aria-label={t("studio.title")} value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} />
        <SaveState s={save} revision={rev.current} t={t} />
        <div className="studio-group">
          <button className="icon-btn" onClick={() => setHist(undo)} disabled={!hist.past.length} title={`${t("studio.undo")} (Ctrl+Z)`} aria-label={t("studio.undo")}>↶</button>
          <button className="icon-btn" onClick={() => setHist(redo)} disabled={!hist.future.length} title={`${t("studio.redo")} (Ctrl+Shift+Z)`} aria-label={t("studio.redo")}>↷</button>
        </div>
        <div className="studio-group">
          <button className="icon-btn" onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z / 1.5))} aria-label={t("studio.zoom_out")}>−</button>
          <span className="studio-zoom" aria-live="polite">{t("studio.zoom", { px: Math.round(zoom) })}</span>
          <button className="icon-btn" onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z * 1.5))} aria-label={t("studio.zoom_in")}>+</button>
          <label className="studio-check"><input type="checkbox" checked={snapOn} onChange={(e) => setSnapOn(e.target.checked)} />{t("studio.snap")}</label>
        </div>
        <div className="studio-group studio-transport">
          <button className="btn small" onClick={() => (preview.playing ? preview.stop() : preview.play(playhead, total, setPlayhead))}
            disabled={preview.loading || (!preview.playing && total === 0)}>
            {preview.loading ? t("studio.preview_loading") : preview.playing ? t("studio.stop") : t("studio.preview")}</button>
          <span className="studio-tc" aria-label={t("studio.playhead")}>{timecode(playhead, fs)}</span>
          <span className="muted">/ {timecode(total, fs)}</span>
        </div>
      </div>

      {(save.kind === "conflict" || save.kind === "invalid" || save.kind === "error") && (
        <div className={`notice ${save.kind === "error" ? "warn" : "bad"}`} role="alert">
          {save.kind === "conflict" ? <>{t("studio.conflict")} <button className="btn small ghost" onClick={onReload}>{t("studio.load_latest")}</button></>
            : <ErrorBox error={save.error} />}
        </div>
      )}
      <PreviewInfo p={preview} profiles={profiles} busCount={project.bus_count} t={t} />

      <div className="studio-main">
        <div className="studio-timeline">
          <div className="studio-heads">
            <div className="studio-ruler-head" />
            {state.tracks.map((tr) => (
              <div key={tr.id} className={`studio-head tone-${BUS_TONE[tr.bus]}${selTrack?.id === tr.id ? " is-sel" : ""}`}
                onClick={() => setSel({ track: tr.id })}>
                <span className="studio-bus">{t(`studio.bus.${tr.bus}`)}</span>
                <span className="studio-head-btns">
                  <button className={`chip-btn${tr.mute ? " on" : ""}`} aria-pressed={tr.mute} title={t("studio.mute")}
                    onClick={(e) => { e.stopPropagation(); commit(updateTrack(state, tr.id, { mute: !tr.mute })); }}>M</button>
                  <button className={`chip-btn${tr.solo ? " on" : ""}`} aria-pressed={tr.solo} title={t("studio.solo")}
                    onClick={(e) => { e.stopPropagation(); commit(updateTrack(state, tr.id, { solo: !tr.solo })); }}>S</button>
                </span>
              </div>
            ))}
          </div>
          <div className="studio-scroll">
            <div className="studio-canvas" style={{ width: timelineFrames * pxPerFrame }} ref={lanes}>
              <div className="studio-ruler" onClick={seek} aria-hidden>
                {ticks.map((f) => <span key={f} style={{ left: f * pxPerFrame }}>{timecode(f, fs, false)}</span>)}
              </div>
              {state.tracks.map((tr) => (
                <div key={tr.id} data-lane={tr.id} className={`studio-lane${selTrack?.id === tr.id ? " is-sel" : ""}`}
                  onPointerDown={(e) => { if (e.target === e.currentTarget) { setSel({ track: tr.id }); seek(e); } }}>
                  {tr.clips.map((c) => {
                    const gen = c.generator_id ? state.generators.find((g) => g.id === c.generator_id) : undefined;
                    const w = Math.max(3, c.duration_frames * pxPerFrame);
                    return (
                      <div key={c.id} role="button" tabIndex={0} aria-pressed={sel.clip === c.id}
                        aria-label={`${clipLabel(c, gen, assets, t)}, ${timecode(c.start_frame, fs)}`}
                        className={`studio-clip tone-${BUS_TONE[tr.bus]}${gen ? " is-gen" : ""}${c.mute ? " is-muted" : ""}${sel.clip === c.id ? " is-sel" : ""}`}
                        style={{ left: c.start_frame * pxPerFrame, width: w }}
                        onPointerDown={(e) => onClipPointerDown(e, c, tr.id, "move")}
                        onFocus={() => setSel({ clip: c.id, track: tr.id })}>
                        {c.asset_id && assets[c.asset_id] && <Waveform asset={assets[c.asset_id]} clip={c} fs={fs} width={w} />}
                        {(c.fade_in_frames ?? 0) > 0 && <span className="fade in" style={{ width: (c.fade_in_frames ?? 0) * pxPerFrame }} />}
                        {(c.fade_out_frames ?? 0) > 0 && <span className="fade out" style={{ width: (c.fade_out_frames ?? 0) * pxPerFrame }} />}
                        <span className="studio-clip-label">{clipLabel(c, gen, assets, t)}</span>
                        {c.asset_id && <span className="trim start" onPointerDown={(e) => onClipPointerDown(e, c, tr.id, "trim-start")} />}
                        <span className="trim end" onPointerDown={(e) => onClipPointerDown(e, c, tr.id, "trim-end")} />
                      </div>
                    );
                  })}
                </div>
              ))}
              <div className="studio-playhead" style={{ left: playhead * pxPerFrame }} aria-hidden />
            </div>
          </div>
        </div>

        <aside className="studio-side">
          <Inspector state={state} sel={sel} fs={fs} assets={assets} commit={commit} playhead={playhead}
            onDelete={(id) => { commit(removeClip(state, id)); setSel({ track: sel.track }); }} />
          <section className="stack" aria-labelledby="gen-h">
            <h3 id="gen-h">{t("studio.generators")}</h3>
            <p className="hint">{t("studio.generators_hint")}</p>
            <div className="studio-row">
              <button className="btn small ghost" disabled={!hasNeuroPair}
                onClick={() => commit(addBinauralPair(state, playhead, 60 * fs))}>{t("studio.add_binaural")}</button>
              <button className="btn small ghost" disabled={!selTrack}
                onClick={() => selTrack && commit(addIsochronic(state, selTrack.id, playhead, 60 * fs))}>{t("studio.add_isochronic")}</button>
            </div>
          </section>
          <SourceLibrary compact onPick={placeAsset} />
          <p className="hint">{t("studio.place_hint", { track: selTrack ? t(`studio.bus.${selTrack.bus}`) : "—", at: timecode(playhead, fs) })}</p>
          <details className="studio-keys"><summary>{t("studio.shortcuts")}</summary><p className="hint">{t("studio.shortcuts_text")}</p></details>
        </aside>
      </div>

      <RenderPanel project={project} profiles={profiles} state={state} unsaved={state !== saved.current || save.kind !== "saved"}
        onClipChange={(on_clip) => commit({ ...state, output: { on_clip } })} />
    </div>
  );
}

function sourceFrames(a: SourceAsset | undefined, fs: number): number {
  return a?.duration ? Math.floor(a.duration * fs) : 0;
}

function clipLabel(c: Clip, gen: State["generators"][number] | undefined, assets: Record<string, SourceAsset>, t: (k: string, p?: any) => string) {
  if (gen?.type === "binaural") {
    const p = gen.params, f = c.generator_output === "left" ? p.carrier_hz - p.delta_hz / 2 : p.carrier_hz + p.delta_hz / 2;
    return t("studio.clip_binaural", { hz: +f.toFixed(3), delta: +p.delta_hz });
  }
  if (gen?.type === "isochronic") return t("studio.clip_isochronic", { hz: +gen.params.carrier_hz, rate: +gen.params.rate_hz });
  return (c.asset_id && assets[c.asset_id]?.name) || t("studio.clip_source");
}

function SaveState({ s, revision, t }: { s: Save; revision: number; t: (k: string, p?: any) => string }) {
  const text = s.kind === "saved" ? t("studio.saved", { n: revision }) : s.kind === "saving" ? t("studio.saving")
    : s.kind === "dirty" ? t("studio.unsaved") : s.kind === "conflict" ? t("studio.conflict_short")
    : s.kind === "invalid" ? t("studio.invalid_short") : t("studio.save_failed");
  return <span className={`studio-save is-${s.kind}`} role="status">{text}</span>;
}

// ------------------------------------------------------------------------------------------- inspector

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return <label className="field">{label}{children}{hint && <span className="hint">{hint}</span>}</label>;
}

function NumberInput({ value, onCommit, step = "any", min, max, suffix }: { value: number; onCommit: (v: number) => void; step?: string;
  min?: number; max?: number; suffix?: string }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const done = () => { const n = Number(v); if (Number.isFinite(n) && v.trim() !== "") onCommit(n); else setV(String(value)); };
  return (
    <span className="studio-num">
      <input inputMode="decimal" value={v} step={step} min={min} max={max} onChange={(e) => setV(e.target.value)} onBlur={done}
        onKeyDown={(e) => { if (e.key === "Enter") { (e.target as HTMLInputElement).blur(); } }} />
      {suffix && <span className="muted">{suffix}</span>}
    </span>
  );
}

function Inspector({ state, sel, fs, assets, commit, onDelete, playhead }: { state: State; sel: { clip?: string; track?: string }; fs: number;
  assets: Record<string, SourceAsset>; commit: (s: State) => void; onDelete: (id: string) => void; playhead: number }) {
  const { t } = useApp();
  const hit = sel.clip ? findClip(state, sel.clip) : null;
  if (!hit) return <section className="studio-inspector"><h3>{t("studio.inspector")}</h3><p className="hint">{t("studio.inspector_empty")}</p></section>;
  const c = hit.clip;
  const gen = c.generator_id ? state.generators.find((g) => g.id === c.generator_id) : undefined;
  const a = c.asset_id ? assets[c.asset_id] : undefined;
  const sec = (f: number) => +(f / fs).toFixed(6);
  const set = (p: Partial<Clip>) => commit(updateClip(state, c.id, p));
  const setGen = (p: Record<string, any>) => gen && commit(updateGenerator(state, gen.id, p));
  const canSplit = !gen && playhead > c.start_frame && playhead < c.start_frame + c.duration_frames;
  return (
    <section className="studio-inspector stack" aria-labelledby="insp-h">
      <h3 id="insp-h">{t("studio.inspector")}</h3>
      <p className="muted">{t(`studio.bus.${hit.track.bus}`)} · {a ? `${a.name} — ${a.channels} ch, ${a.sample_rate} Hz` : gen ? t(`studio.gen_${gen.type}`) : ""}</p>
      {a && a.sample_rate !== fs && <div className="notice warn">{t("studio.src_note", { from: a.sample_rate ?? 0, to: fs })}</div>}
      <div className="form">
        <Field label={t("studio.start")} hint={t("studio.frames_n", { n: c.start_frame })}>
          <NumberInput value={sec(c.start_frame)} min={0} suffix="s" onCommit={(v) => set({ start_frame: Math.round(v * fs) })} /></Field>
        <Field label={t("studio.length")} hint={t("studio.frames_n", { n: c.duration_frames })}>
          <NumberInput value={sec(c.duration_frames)} min={0} suffix="s" onCommit={(v) => set({ duration_frames: Math.max(1, Math.round(v * fs)) })} /></Field>
        <Field label={t("studio.gain")} hint={t("studio.gain_hint")}>
          <NumberInput value={c.gain_db ?? 0} min={-96} max={24} suffix="dB" onCommit={(v) => set({ gain_db: Math.max(-96, Math.min(24, v)) })} /></Field>
        <Field label={t("studio.fade_in")}><NumberInput value={sec(c.fade_in_frames ?? 0)} min={0} suffix="s" onCommit={(v) => set({ fade_in_frames: Math.round(v * fs) })} /></Field>
        <Field label={t("studio.fade_out")}><NumberInput value={sec(c.fade_out_frames ?? 0)} min={0} suffix="s" onCommit={(v) => set({ fade_out_frames: Math.round(v * fs) })} /></Field>
        {a && (a.channels ?? 1) > 1 && <Field label={t("studio.source_channel")}>
          <select value={c.source_channel ?? 0} onChange={(e) => set({ source_channel: Number(e.target.value) })}>
            {Array.from({ length: a.channels ?? 1 }, (_, i) => <option key={i} value={i}>{t("studio.channel_n", { n: i + 1 })}</option>)}</select></Field>}
        {a && <Field label={t("studio.source_offset")} hint={t("studio.frames_n", { n: c.source_offset_frame ?? 0 })}>
          <NumberInput value={sec(c.source_offset_frame ?? 0)} min={0} suffix="s" onCommit={(v) => set({ source_offset_frame: Math.max(0, Math.round(v * fs)) })} /></Field>}
        {gen && <Field label={t("studio.gen_output")}>
          <select value={c.generator_output ?? "mono"} onChange={(e) => set({ generator_output: e.target.value as Clip["generator_output"] })}>
            {(gen.type === "binaural" ? ["left", "right"] : ["mono"]).map((o) => <option key={o} value={o}>{t(`studio.out_${o}`)}</option>)}</select></Field>}
      </div>
      {gen && <GeneratorFields gen={gen} fs={fs} onChange={setGen} />}
      <label className="studio-check"><input type="checkbox" checked={!!c.mute} onChange={(e) => set({ mute: e.target.checked })} />{t("studio.mute_clip")}</label>
      <div className="studio-row">
        <button className="btn small ghost" disabled={!canSplit} onClick={() => commit(splitClip(state, c.id, playhead))}
          title={gen ? t("studio.split_gen") : undefined}>{t("studio.split")}</button>
        <button className="btn small danger" onClick={() => onDelete(c.id)}>{t("studio.delete")}</button>
      </div>
    </section>
  );
}

function GeneratorFields({ gen, fs, onChange }: { gen: State["generators"][number]; fs: number; onChange: (p: Record<string, any>) => void }) {
  const { t } = useApp();
  const p = gen.params;
  const num = (k: string, label: string, suffix: string, hint?: string) => (
    <Field key={k} label={label} hint={hint}><NumberInput value={p[k]} suffix={suffix} onCommit={(v) => onChange({ [k]: v })} /></Field>
  );
  return (
    <div className="form">
      {num("carrier_hz", t("studio.carrier"), "Hz")}
      {gen.type === "binaural" ? num("delta_hz", t("studio.delta"), "Hz", t("studio.delta_hint", { l: +(p.carrier_hz - p.delta_hz / 2).toFixed(3), r: +(p.carrier_hz + p.delta_hz / 2).toFixed(3) }))
        : <>{num("rate_hz", t("studio.rate"), "Hz")}{num("duty", t("studio.duty"), "")}{num("edge_ms", t("studio.edge"), "ms")}</>}
      {num("amplitude_dbfs", t("studio.amplitude"), "dBFS", t("studio.amplitude_hint"))}
      <Field label={t("studio.ramp")} hint={t("studio.frames_n", { n: p.ramp_frames ?? 0 })}>
        <NumberInput value={+((p.ramp_frames ?? 0) / fs).toFixed(6)} suffix="s" onCommit={(v) => onChange({ ramp_frames: Math.max(0, Math.round(v * fs)) })} /></Field>
      {gen.type === "binaural" && <Automation gen={gen} fs={fs} onChange={onChange} field="delta_automation" />}
      {gen.type === "binaural" && <Automation gen={gen} fs={fs} onChange={onChange} field="carrier_automation" />}
    </div>
  );
}

/** Two-point linear automation (spec §4.1): a ramp of the beat (Δ) or of the carrier from one value to another.
 * The renderer integrates the frequency exactly, so the ramp is phase-continuous. */
type AutoField = "delta_automation" | "carrier_automation";
const AUTO: Record<AutoField, { base: "delta_hz" | "carrier_hz"; label: string; min: number; max: number; end: (v: number) => number }> = {
  delta_automation: { base: "delta_hz", label: "studio.delta_ramp", min: 0.0001, max: 100, end: (v) => Math.max(0.5, v / 2) },
  carrier_automation: { base: "carrier_hz", label: "studio.carrier_ramp", min: 20, max: 1500, end: (v) => Math.min(1500, v + 50) },
};

function Automation({ gen, fs, onChange, field = "delta_automation" }: { gen: State["generators"][number]; fs: number;
  onChange: (p: Record<string, any>) => void; field?: AutoField }) {
  const { t } = useApp();
  const cfg = AUTO[field];
  const pts: { frame: number; value: number }[] = gen.params[field] ?? [];
  const on = pts.length >= 2;
  const set = (a: { frame: number; value: number }, b: { frame: number; value: number }) => onChange({ [field]: [a, b] });
  const order = on && pts[1].frame <= pts[0].frame;
  const range = on && pts.some((p) => !(p.value >= cfg.min && p.value <= cfg.max));
  return (
    <fieldset className={`studio-auto${on ? "" : " off"}`}>
      <legend><label className="studio-check"><input type="checkbox" checked={on}
        onChange={(e) => onChange({ [field]: e.target.checked ? [{ frame: 0, value: gen.params[cfg.base] }, { frame: 300 * fs, value: cfg.end(gen.params[cfg.base]) }] : [] })} />
        {t(cfg.label)}</label></legend>
      {on && <>
        <Field label={t("studio.ramp_from")} hint={t("studio.frames_n", { n: pts[0].frame })}>
          <span className="studio-pair">
            <NumberInput value={+(pts[0].frame / fs).toFixed(3)} suffix="s" onCommit={(v) => set({ ...pts[0], frame: Math.max(0, Math.round(v * fs)) }, pts[1])} />
            <NumberInput value={pts[0].value} suffix="Hz" onCommit={(v) => set({ ...pts[0], value: v }, pts[1])} />
          </span></Field>
        <Field label={t("studio.ramp_to")} hint={t("studio.frames_n", { n: pts[1].frame })}>
          <span className="studio-pair">
            <NumberInput value={+(pts[1].frame / fs).toFixed(3)} suffix="s" onCommit={(v) => set(pts[0], { ...pts[1], frame: Math.max(1, Math.round(v * fs)) })} />
            <NumberInput value={pts[1].value} suffix="Hz" onCommit={(v) => set(pts[0], { ...pts[1], value: v })} />
          </span></Field>
        {order && <p className="notice warn">{t("studio.ramp_order")}</p>}
        {range && <p className="notice warn">{t("studio.ramp_range", { min: cfg.min, max: cfg.max })}</p>}
      </>}
    </fieldset>
  );
}

// ------------------------------------------------------------------------------------------- preview

type PreviewApi = ReturnType<typeof usePreview>;

/** Server-rendered preview window (≤ 60 s from the playhead) played through one AudioContext. */
function usePreview(projectId: string, fs: number) {
  const ctx = useRef<AudioContext | null>(null);
  const node = useRef<AudioBufferSourceNode | null>(null);
  const raf = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [profile, setProfile] = useState<string | null>(null);
  const [info, setInfo] = useState<{ maxChannels: number; contextRate: number; channels: number } | null>(null);

  const context = () => {
    if (!ctx.current) {
      try { ctx.current = new AudioContext({ sampleRate: fs }); } catch { ctx.current = new AudioContext(); }
    }
    return ctx.current;
  };
  const stop = useCallback(() => {
    try { node.current?.stop(); } catch { /* already stopped */ }
    node.current = null; cancelAnimationFrame(raf.current); setPlaying(false);
  }, []);
  useEffect(() => () => { stop(); ctx.current?.close().catch(() => {}); }, [stop]);

  const play = useCallback(async (from: number, total: number, onPos: (f: number) => void, chosen?: string) => {
    const ac = context();
    const maxCh = ac.destination.maxChannelCount || 2;
    const prof = chosen ?? profile ?? "HEADPHONES_2_0";
    const frames = Math.min(60 * fs, Math.max(0, total - from));
    if (frames <= 0) return;
    setLoading(true); setError(null);
    try {
      const res = await fetch(`/api/v1/audio/projects/${projectId}/preview`, { method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json", "x-csrf-token": csrfToken() }, body: JSON.stringify({ start_frame: from, frames, profile: prof }) });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new ApiError(res.status, j.error?.code ?? "preview_failed", j.error?.message ?? res.statusText, j.error?.request_id, j.error?.details);
      }
      const buf = await ac.decodeAudioData(await res.arrayBuffer());
      if (buf.numberOfChannels <= maxCh) {
        ac.destination.channelCount = buf.numberOfChannels;
        ac.destination.channelInterpretation = "discrete";   // no browser up/down-mix of the rendered channels
      }
      setInfo({ maxChannels: maxCh, contextRate: ac.sampleRate, channels: buf.numberOfChannels });
      await ac.resume();
      stop();
      const src = ac.createBufferSource();
      src.buffer = buf; src.channelInterpretation = "discrete"; src.connect(ac.destination);
      const t0 = ac.currentTime;
      src.onended = () => { if (node.current === src) stop(); };
      src.start();
      node.current = src; setPlaying(true);
      const tick = () => { onPos(from + Math.min(frames, Math.round((ac.currentTime - t0) * fs))); raf.current = requestAnimationFrame(tick); };
      raf.current = requestAnimationFrame(tick);
    } catch (e) { setError(e); } finally { setLoading(false); }
  }, [fs, profile, projectId, stop]);

  return { play, stop, playing, loading, error, profile: profile ?? "HEADPHONES_2_0", setProfile, info };
}

function csrfToken() {
  const m = document.cookie.match(/(?:^|; )nap_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function PreviewInfo({ p, profiles, busCount, t }: { p: PreviewApi; profiles: Profile[]; busCount: number; t: (k: string, q?: any) => string }) {
  const chosen = profiles.find((x) => x.key === p.profile);
  return (
    <div className="studio-preview">
      <label className="studio-inline">{t("studio.preview_profile")}
        <select value={p.profile} onChange={(e) => { p.stop(); p.setProfile(e.target.value); }}>
          {profiles.map((x) => <option key={x.key} value={x.key}>{x.name}</option>)}</select></label>
      {p.info && <span className="muted">{t("studio.device_channels", { n: p.info.maxChannels, rate: p.info.contextRate })}</span>}
      {p.info && chosen && chosen.channels.length > p.info.maxChannels && <span className="badge warn">{t("studio.device_downmix", { n: chosen.channels.length })}</span>}
      {busCount > 2 && p.profile === "HEADPHONES_2_0" && <span className="hint">{t("studio.preview_fold_hint")}</span>}
      {p.error ? <ErrorBox error={p.error} /> : null}
    </div>
  );
}
