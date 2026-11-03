"use client";

import { useState } from "react";
import { useApp } from "@/components/Providers";

// NAP-108/109: a short tone in each channel in turn, labelled, so the listener can check that every speaker plays
// from where it should. Uses the browser output with exactly n discrete channels.
export function SpeakerTest({ order }: { order: string[] }) {
  const { t } = useApp();
  const [now, setNow] = useState<string | null>(null);
  const run = async () => {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    const ctx: AudioContext = new Ctx();
    const n = order.length;
    ctx.destination.channelCount = Math.min(n, ctx.destination.maxChannelCount || 2);
    ctx.destination.channelCountMode = "explicit";
    ctx.destination.channelInterpretation = "discrete";
    const merger = ctx.createChannelMerger(n);
    merger.connect(ctx.destination);
    for (let i = 0; i < n; i++) {
      setNow(order[i]);
      const osc = ctx.createOscillator(), g = ctx.createGain(), at = ctx.currentTime;
      osc.frequency.value = order[i] === "LFE" ? 60 : 440;
      g.gain.setValueAtTime(0, at); g.gain.linearRampToValueAtTime(0.15, at + 0.05); g.gain.linearRampToValueAtTime(0, at + 0.9);
      osc.connect(g).connect(merger, 0, i);
      osc.start(at); osc.stop(at + 1);
      await new Promise((r) => setTimeout(r, 1200));
    }
    setNow(null);
    ctx.close().catch(() => {});
  };
  return (
    <div className="row" data-testid="speaker-test">
      <button type="button" className="btn ghost small" disabled={!!now} onClick={run}>{t("player.speaker_test")}</button>
      <span role="status" className="hint">{now ? t("player.speaker_now", { ch: t(`ch.${now}`) }) : t("player.speaker_hint")}</span>
    </div>
  );
}

