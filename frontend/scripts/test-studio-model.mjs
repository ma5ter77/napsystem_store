// Unit tests of the Audio Studio editing model (frame-exact operations, undo/redo). Node ≥ 22.18 strips types.
import assert from "node:assert/strict";
import test from "node:test";
import * as m from "../components/studio/model.ts";

const FS = 48000;
const base = () => ({ tracks: [
  { id: "t1", name: "", bus: "NEURO_LEFT", mute: false, solo: false, clips: [] },
  { id: "t2", name: "", bus: "NEURO_RIGHT", mute: false, solo: false, clips: [] },
  { id: "t3", name: "", bus: "VOICE_CENTER", mute: false, solo: false, clips: [] }], generators: [], output: { on_clip: "fail" } });

test("snap keeps integer, non-negative frames", () => {
  assert.equal(m.snap(-5, 0), 0);
  assert.equal(m.snap(1234.6, 0), 1235);
  assert.equal(m.snap(70000, 48000), 48000);
  assert.equal(m.snap(73000, 48000), 96000);
});

test("split joins sample-exactly and shifts the source offset", () => {
  let [s, id] = m.addClip(base(), "t3", { start_frame: 1000, duration_frames: 10000, asset_id: "a", source_offset_frame: 50, fade_in_frames: 100, fade_out_frames: 200 });
  s = m.splitClip(s, id, 4000);
  const [a, b] = s.tracks[2].clips;
  assert.equal(a.start_frame + a.duration_frames, b.start_frame);
  assert.equal(a.duration_frames + b.duration_frames, 10000);
  assert.equal(b.source_offset_frame, 50 + 3000);
  assert.equal(a.fade_out_frames, 0); assert.equal(b.fade_in_frames, 0);
  assert.equal(a.fade_in_frames, 100); assert.equal(b.fade_out_frames, 200);
  assert.deepEqual(m.splitClip(s, id, 1000), s, "split at an edge is a no-op");
});

test("generator clips are never split (phase would restart)", () => {
  const s = m.addBinauralPair(base(), 0, 60 * FS);
  const id = s.tracks[0].clips[0].id;
  assert.equal(m.splitClip(s, id, 30 * FS), s);
});

test("binaural pair lands on neuro L/R with the same timing and one generator", () => {
  const s = m.addBinauralPair(base(), 480, 60 * FS);
  const [l] = s.tracks[0].clips, [r] = s.tracks[1].clips;
  assert.equal(s.generators.length, 1);
  assert.equal(l.generator_id, r.generator_id);
  assert.deepEqual([l.generator_output, r.generator_output], ["left", "right"]);
  assert.deepEqual([l.start_frame, l.duration_frames], [r.start_frame, r.duration_frames]);
  assert.notEqual(l.id, r.id);
});

test("fades never exceed the clip and removing clips prunes unused generators", () => {
  let [s, id] = m.addClip(base(), "t3", { start_frame: 0, duration_frames: 100, asset_id: "a" });
  s = m.updateClip(s, id, { fade_in_frames: 80, fade_out_frames: 80 });
  const c = m.findClip(s, id).clip;
  assert.ok(c.fade_in_frames + c.fade_out_frames <= c.duration_frames);
  s = m.addBinauralPair(s, 0, FS);
  for (const tr of s.tracks) for (const x of [...tr.clips]) if (x.generator_id) s = m.removeClip(s, x.id);
  assert.equal(s.generators.length, 0);
});

test("move to another track keeps id and timing", () => {
  let [s, id] = m.addClip(base(), "t1", { start_frame: 777, duration_frames: 10, asset_id: "a" });
  s = m.moveClipToTrack(s, id, "t3");
  assert.equal(s.tracks[0].clips.length, 0);
  assert.deepEqual(s.tracks[2].clips[0], { ...m.findClip(s, id).clip, id, start_frame: 777, duration_frames: 10 });
});

test("undo/redo walk the history and a new edit clears redo", () => {
  let h = m.historyInit(base());
  const s1 = m.addClip(h.present, "t1", { start_frame: 0, duration_frames: 1, asset_id: "a" })[0];
  h = m.historyPush(h, s1);
  const s2 = m.updateTrack(h.present, "t1", { mute: true });
  h = m.historyPush(h, s2);
  h = m.undo(h); assert.equal(h.present, s1);
  h = m.undo(h); assert.equal(h.present.tracks[0].clips.length, 0);
  h = m.redo(h); assert.equal(h.present, s1);
  h = m.historyPush(h, m.updateTrack(h.present, "t2", { solo: true }));
  assert.equal(h.future.length, 0);
});

test("timecode and duration", () => {
  assert.equal(m.timecode(48000 * 3723.25, FS), "1:02:03.250");
  assert.equal(m.timecode(0, FS, false), "0:00");
  const s = m.addClip(base(), "t3", { start_frame: 119 * 60 * FS, duration_frames: 60 * FS, asset_id: "a" })[0];
  assert.equal(m.durationFrames(s), 120 * 60 * FS);
});
