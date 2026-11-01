// NAP Audio Studio (spec v2.1): pure editing operations on the project state. All timing is in integer frames
// at the project sample rate; the server re-validates every saved state.

export type Bus = "NEURO_LEFT" | "NEURO_RIGHT" | "VOICE_CENTER" | "MUSIC_LEFT" | "MUSIC_RIGHT" | "LFE" | "BACK_LEFT" | "BACK_RIGHT";
export const BUSES: Bus[] = ["NEURO_LEFT", "NEURO_RIGHT", "VOICE_CENTER", "MUSIC_LEFT", "MUSIC_RIGHT", "LFE", "BACK_LEFT", "BACK_RIGHT"];

export type Clip = {
  id: string; start_frame: number; duration_frames: number;
  asset_id?: string | null; source_channel?: number; source_offset_frame?: number;
  generator_id?: string | null; generator_output?: "left" | "right" | "mono";
  gain_db?: number; fade_in_frames?: number; fade_out_frames?: number; mute?: boolean;
};
export type Track = { id: string; name: string; bus: Bus; mute: boolean; solo: boolean; clips: Clip[] };
export type Generator = { id: string; type: "binaural" | "isochronic"; version?: number; params: Record<string, any> };
export type State = { tracks: Track[]; generators: Generator[]; output: { on_clip: "fail" | "hard_clip" } };

export function newId(prefix: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  for (let i = 1; ; i++) if (!used.has(`${prefix}${i}`)) return `${prefix}${i}`;
}

export function allIds(s: State): string[] {
  return [...s.tracks.map((t) => t.id), ...s.tracks.flatMap((t) => t.clips.map((c) => c.id))];
}

export function durationFrames(s: State): number {
  return Math.max(0, ...s.tracks.flatMap((t) => t.clips.map((c) => c.start_frame + c.duration_frames)));
}

/** Snap a frame to the grid (grid ≤ 0 means no snapping). Always returns a non-negative integer. */
export function snap(frame: number, grid: number): number {
  const f = Math.max(0, Math.round(frame));
  return grid > 0 ? Math.round(f / grid) * grid : f;
}

export function findClip(s: State, clipId: string): { track: Track; clip: Clip } | null {
  for (const track of s.tracks) {
    const clip = track.clips.find((c) => c.id === clipId);
    if (clip) return { track, clip };
  }
  return null;
}

function mapTracks(s: State, fn: (t: Track) => Track): State {
  return { ...s, tracks: s.tracks.map(fn) };
}

export function updateClip(s: State, clipId: string, patch: Partial<Clip>): State {
  return mapTracks(s, (t) => ({ ...t, clips: t.clips.map((c) => (c.id === clipId ? clampFades({ ...c, ...patch }) : c)) }));
}

function clampFades(c: Clip): Clip {
  const d = Math.max(1, Math.round(c.duration_frames));
  let fi = Math.max(0, Math.round(c.fade_in_frames ?? 0)), fo = Math.max(0, Math.round(c.fade_out_frames ?? 0));
  if (fi + fo > d) { const k = d / (fi + fo); fi = Math.floor(fi * k); fo = Math.floor(fo * k); }
  return { ...c, start_frame: Math.max(0, Math.round(c.start_frame)), duration_frames: d, fade_in_frames: fi, fade_out_frames: fo };
}

export function updateTrack(s: State, trackId: string, patch: Partial<Track>): State {
  return mapTracks(s, (t) => (t.id === trackId ? { ...t, ...patch } : t));
}

export function addClip(s: State, trackId: string, clip: Omit<Clip, "id">): [State, string] {
  const id = newId("c", allIds(s));
  return [mapTracks(s, (t) => (t.id === trackId ? { ...t, clips: [...t.clips, clampFades({ ...clip, id })] } : t)), id];
}

export function removeClip(s: State, clipId: string): State {
  const next = mapTracks(s, (t) => ({ ...t, clips: t.clips.filter((c) => c.id !== clipId) }));
  return pruneGenerators(next);
}

/** Moves a clip to another track (keeps its id and timing). */
export function moveClipToTrack(s: State, clipId: string, trackId: string): State {
  const hit = findClip(s, clipId);
  if (!hit || hit.track.id === trackId) return s;
  const without = mapTracks(s, (t) => ({ ...t, clips: t.clips.filter((c) => c.id !== clipId) }));
  return mapTracks(without, (t) => (t.id === trackId ? { ...t, clips: [...t.clips, hit.clip] } : t));
}

/** Splits a clip at an absolute project frame into two clips that join sample-exactly. */
export function splitClip(s: State, clipId: string, at: number): State {
  const hit = findClip(s, clipId);
  if (!hit) return s;
  const c = hit.clip;
  const rel = Math.round(at) - c.start_frame;
  if (rel <= 0 || rel >= c.duration_frames) return s;
  const id = newId("c", allIds(s));
  const left: Clip = { ...c, duration_frames: rel, fade_out_frames: 0 };
  const right: Clip = { ...c, id, start_frame: c.start_frame + rel, duration_frames: c.duration_frames - rel, fade_in_frames: 0,
    ...(c.asset_id ? { source_offset_frame: (c.source_offset_frame ?? 0) + rel } : {}) };
  // A generator's samples are a function of the clip-relative frame (phase and automation start at the clip
  // start), so splitting would restart its phase. Generator clips are therefore not split (the UI says so).
  if (c.generator_id) return s;
  return mapTracks(s, (t) => (t.id === hit.track.id
    ? { ...t, clips: t.clips.flatMap((x) => (x.id === clipId ? [clampFades(left), clampFades(right)] : [x])) } : t));
}

export function addGenerator(s: State, type: Generator["type"]): [State, string] {
  const id = newId("g", s.generators.map((g) => g.id));
  const params = type === "binaural"
    ? { carrier_hz: 200, delta_hz: 8, amplitude_dbfs: -18, ramp_frames: 0 }
    : { carrier_hz: 440, rate_hz: 10, duty: 0.5, edge_ms: 5, amplitude_dbfs: -18, ramp_frames: 0 };
  return [{ ...s, generators: [...s.generators, { id, type, version: 1, params }] }, id];
}

export function updateGenerator(s: State, id: string, params: Record<string, any>): State {
  return { ...s, generators: s.generators.map((g) => (g.id === id ? { ...g, params: { ...g.params, ...params } } : g)) };
}

/** Drops generators no clip refers to any more (the server rejects nothing here, but it keeps the state tidy). */
export function pruneGenerators(s: State): State {
  const used = new Set(s.tracks.flatMap((t) => t.clips.map((c) => c.generator_id).filter(Boolean)));
  return { ...s, generators: s.generators.filter((g) => used.has(g.id)) };
}

/**
 * Places a binaural generator as a stereo pair: the left output on NEURO_LEFT and the right output on
 * NEURO_RIGHT, same start and length, so the beat is formed between the ears (spec §4.1).
 */
export function addBinauralPair(s: State, start: number, frames: number): State {
  const [s1, gid] = addGenerator(s, "binaural");
  const left = s1.tracks.find((t) => t.bus === "NEURO_LEFT"), right = s1.tracks.find((t) => t.bus === "NEURO_RIGHT");
  if (!left || !right) return s;
  const [s2] = addClip(s1, left.id, { start_frame: start, duration_frames: frames, generator_id: gid, generator_output: "left" });
  const [s3] = addClip(s2, right.id, { start_frame: start, duration_frames: frames, generator_id: gid, generator_output: "right" });
  return s3;
}

export function addIsochronic(s: State, trackId: string, start: number, frames: number): State {
  const [s1, gid] = addGenerator(s, "isochronic");
  return addClip(s1, trackId, { start_frame: start, duration_frames: frames, generator_id: gid, generator_output: "mono" })[0];
}

/** Undo/redo history of whole states (states are small JSON documents). */
export type History = { past: State[]; present: State; future: State[] };
export const LIMIT = 200;

export function historyInit(s: State): History {
  return { past: [], present: s, future: [] };
}

export function historyPush(h: History, next: State, coalesce = false): History {
  if (next === h.present) return h;
  if (coalesce && h.past.length) return { past: h.past, present: next, future: [] };
  return { past: [...h.past, h.present].slice(-LIMIT), present: next, future: [] };
}

export function undo(h: History): History {
  if (!h.past.length) return h;
  return { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] };
}

export function redo(h: History): History {
  if (!h.future.length) return h;
  return { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) };
}

/** "1:02:03.250" style timecode for a frame count. */
export function timecode(frames: number, fs: number, ms = true): string {
  const total = frames / fs;
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), sec = total % 60;
  const ss = ms ? sec.toFixed(3).padStart(6, "0") : String(Math.floor(sec)).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Grid step in frames that keeps grid lines at least ~60 px apart for a given zoom (px per second). */
export function gridFrames(pxPerSec: number, fs: number): number {
  const steps = [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800];
  const sec = steps.find((x) => x * pxPerSec >= 60) ?? 3600;
  return Math.round(sec * fs);
}
