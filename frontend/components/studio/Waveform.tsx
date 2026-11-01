"use client";

// Min/max waveform of the part of a source a clip uses (server-computed peaks, cached per asset).
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Clip } from "./model";
import type { SourceAsset } from "./Sources";

type Peaks = { buckets: number; frames: number; channels: { min: number[]; max: number[] }[] };
const cache = new Map<string, Promise<Peaks>>();
const BUCKETS = 4096;

function peaks(id: string): Promise<Peaks> {
  if (!cache.has(id)) {
    const p = api<Peaks>(`/audio/assets/${id}/waveform?buckets=${BUCKETS}`);
    p.catch(() => cache.delete(id));
    cache.set(id, p);
  }
  return cache.get(id)!;
}

export function Waveform({ asset, clip, fs, width }: { asset: SourceAsset; clip: Clip; fs: number; width: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [data, setData] = useState<Peaks | null>(null);
  useEffect(() => { let live = true; peaks(asset.id).then((d) => live && setData(d)).catch(() => {}); return () => { live = false; }; }, [asset.id]);

  const w = Math.min(4096, Math.max(1, Math.round(width)));
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !data) return;
    const h = cv.height, ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    const ch = data.channels[Math.min(clip.source_channel ?? 0, data.channels.length - 1)];
    // clip frames are at the project rate; the peaks cover the whole source → map through the source duration
    const totalProjectFrames = (asset.duration ?? 0) * fs;
    if (!ch || totalProjectFrames <= 0) return;
    const gain = Math.pow(10, (clip.gain_db ?? 0) / 20);
    ctx.fillStyle = getComputedStyle(cv).color;
    const mid = h / 2;
    for (let x = 0; x < w; x++) {
      const f0 = (clip.source_offset_frame ?? 0) + (x / w) * clip.duration_frames;
      const f1 = (clip.source_offset_frame ?? 0) + ((x + 1) / w) * clip.duration_frames;
      const b0 = Math.floor((f0 / totalProjectFrames) * data.buckets), b1 = Math.max(b0 + 1, Math.ceil((f1 / totalProjectFrames) * data.buckets));
      let lo = 0, hi = 0;
      for (let b = b0; b < b1 && b < data.buckets; b++) { lo = Math.min(lo, ch.min[b]); hi = Math.max(hi, ch.max[b]); }
      const top = mid - Math.min(1, hi * gain) * mid, bot = mid - Math.max(-1, lo * gain) * mid;
      ctx.fillRect(x, top, 1, Math.max(1, bot - top));
    }
  }, [data, clip.source_offset_frame, clip.duration_frames, clip.source_channel, clip.gain_db, asset.duration, fs, w]);

  return <canvas ref={ref} className="studio-wave" width={w} height={56} aria-hidden />;
}
