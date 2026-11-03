"use client";

// NAP-109: what this browser can play right now — read from the browser itself, nothing is sent anywhere.
import { useEffect, useState } from "react";
import { useApp } from "@/components/Providers";
import { SpeakerTest } from "@/components/SpeakerTest";
import { detectOutput } from "@/lib/playback";

const LAYOUTS: { key: string; channels: number; order: string[] }[] = [
  { key: "headphones", channels: 2, order: ["FL", "FR"] },
  { key: "acoustic_3_0", channels: 3, order: ["FL", "FR", "FC"] },
  { key: "surround_5_1", channels: 6, order: ["FL", "FR", "FC", "LFE", "SL", "SR"] },
  { key: "surround_7_1", channels: 8, order: ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"] },
];

type Facts = { channels: number; flac: boolean; webaudio: boolean; offline: boolean };

export function PlaybackCheck() {
  const { t } = useApp();
  const [f, setF] = useState<Facts | null>(null);
  useEffect(() => {
    const a = document.createElement("audio");
    setF({
      channels: detectOutput().maxChannels,
      flac: !!(a.canPlayType("audio/flac") || a.canPlayType("audio/x-flac")),
      webaudio: !!(window.AudioContext || (window as any).webkitAudioContext),
      offline: typeof caches !== "undefined" && window.isSecureContext,
    });
  }, []);
  if (!f) return <section className="panel" aria-busy="true"><p className="muted">{t("help.checking")}</p></section>;
  const ready = f.flac && f.webaudio;
  const best = [...LAYOUTS].reverse().find((l) => l.channels <= f.channels) ?? LAYOUTS[0];
  const yes = (ok: boolean) => <span className={`badge ${ok ? "ok" : "bad"}`}>{ok ? t("help.yes") : t("help.no")}</span>;
  return (
    <section className="panel stack" aria-labelledby="h-check" data-testid="playback-check">
      <h2 id="h-check">{t("help.check_title")}</h2>
      <dl className="kv">
        <dt>{t("help.output")}</dt><dd data-testid="check-channels">{t("help.output_value", { n: f.channels })}</dd>
        <dt>{t("help.flac")}</dt><dd>{yes(f.flac)}</dd>
        <dt>{t("help.webaudio")}</dt><dd>{yes(f.webaudio)}</dd>
        <dt>{t("help.offline")}</dt><dd>{yes(f.offline)}</dd>
      </dl>
      <ul className="checklist" aria-label={t("help.layouts")}>{LAYOUTS.map((l) => {
        const ok = ready && l.channels <= f.channels;
        return <li key={l.key} className={ok ? "ok" : "warn"}><span aria-hidden="true">{ok ? "✓" : "–"}</span>
          <span>{t(`layout.${l.key}`)} — {ok ? t("help.can_play") : !ready ? t("help.need_browser") : t("help.need_channels", { n: l.channels })}</span></li>;
      })}</ul>
      <p className={ready ? "hint" : "notice warn"}>{ready ? t("help.verdict", { layout: t(`layout.${best.key}`) }) : t("help.update_browser")}</p>
      {ready && f.channels > 2 && <SpeakerTest order={best.order} />}
    </section>
  );
}
