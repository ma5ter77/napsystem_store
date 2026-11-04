"use client";

// NAP-101: output calibration at the listening place. Pink noise plays in one channel at a time with the current trim,
// the listener evens out the levels (±6 dB) and, for speakers closer than the others, adds delay (0–20 ms;
// 1 ms ≈ 34 cm). Saved to the listener's device for this layout and applied by the player on every session.
// Only level and arrival time change: the channel's signal, and the binaural difference inside it, stay as they are.
import { useEffect, useRef, useState } from "react";
import { ChannelDiagram, type DiagramLayout } from "@/components/ChannelDiagram";
import { useApp } from "@/components/Providers";
import { ErrorBox, useAction } from "@/components/ui";
import { api } from "@/lib/api";
import type { ChannelTrim } from "@/lib/playback";

const GAIN = 6, DELAY = 20, NOISE_S = 1.4;

function pinkNoise(ctx: BaseAudioContext, seconds: number): AudioBuffer {
  const n = Math.floor(ctx.sampleRate * seconds), buf = ctx.createBuffer(1, n, ctx.sampleRate), x = buf.getChannelData(0);
  let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  for (let i = 0; i < n; i++) {   // Paul Kellet's filter: -3 dB / octave, equal energy per octave
    const w = Math.random() * 2 - 1;
    b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759; b2 = 0.969 * b2 + w * 0.153852;
    b3 = 0.8665 * b3 + w * 0.3104856; b4 = 0.55 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.016898;
    x[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362) * 0.11; b6 = w * 0.115926;
  }
  return buf;
}

export function Calibration({ deviceId, layout, order, initial, onSaved }: {
  deviceId: string; layout: Exclude<DiagramLayout, "headphones">; order: string[];
  initial?: Record<string, Partial<ChannelTrim>>; onSaved?: (device: any) => void;
}) {
  const { t, locale } = useApp();
  const act = useAction();
  const start = () => Object.fromEntries(order.map((ch) => [ch, { gain_db: Number(initial?.[ch]?.gain_db ?? 0), delay_ms: Number(initial?.[ch]?.delay_ms ?? 0) }]));
  const [trims, setTrims] = useState<Record<string, ChannelTrim>>(start);
  const [now, setNow] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const stop = useRef(false);
  useEffect(() => () => { stop.current = true; }, []);
  const dirty = order.some((ch) => trims[ch].gain_db !== Number(initial?.[ch]?.gain_db ?? 0) || trims[ch].delay_ms !== Number(initial?.[ch]?.delay_ms ?? 0));
  const set = (ch: string, k: keyof ChannelTrim, v: number) => { setSaved(false); setTrims({ ...trims, [ch]: { ...trims[ch], [k]: v } }); };

  const play = async (list: string[]) => {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    const ctx: AudioContext = new Ctx();
    const n = order.length;
    ctx.destination.channelCount = Math.min(n, ctx.destination.maxChannelCount || 2);
    ctx.destination.channelCountMode = "explicit";
    ctx.destination.channelInterpretation = "discrete";
    const merge = ctx.createChannelMerger(n);
    merge.connect(ctx.destination);
    stop.current = false;
    for (const ch of list) {
      if (stop.current) break;
      const i = order.indexOf(ch), x = trims[ch];
      setNow(ch);
      const src = ctx.createBufferSource(), env = ctx.createGain(), trim = ctx.createGain(), dl = ctx.createDelay(0.05);
      src.buffer = pinkNoise(ctx, NOISE_S);
      trim.gain.value = Math.pow(10, x.gain_db / 20);
      dl.delayTime.value = x.delay_ms / 1000;
      const at = ctx.currentTime;
      env.gain.setValueAtTime(0, at); env.gain.linearRampToValueAtTime(1, at + 0.08);
      env.gain.setValueAtTime(1, at + NOISE_S - 0.15); env.gain.linearRampToValueAtTime(0, at + NOISE_S);
      let node: AudioNode = src;
      if (ch === "LFE") { const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 120; src.connect(lp); node = lp; }
      node.connect(env).connect(trim).connect(dl).connect(merge, 0, i);
      src.start(at);
      await new Promise((r) => setTimeout(r, NOISE_S * 1000 + 250));
    }
    setNow(null);
    ctx.close().catch(() => {});
  };

  const save = async (channels: Record<string, ChannelTrim>) => {
    const d = await act.run(() => api<any>(`/devices/${deviceId}/calibration`, { method: "PUT", body: { layout, channels } }));
    if (d) { setSaved(true); onSaved?.(d); }
  };
  const fmt = (v: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(v);

  return (
    <section className="calibration stack" data-testid="calibration" aria-labelledby="cal-h">
      <h3 id="cal-h">{t("cal.title")}</h3>
      <p className="hint">{t("cal.lead")}</p>
      <div className="cal-body">
        <ChannelDiagram layout={layout} active={now} idPrefix="cal" onPick={(ch) => play([ch])} />
        <div className="scroll-x"><table className="channel-map cal-table">
          <thead><tr><th>{t("lay.channel")}</th><th>{t("cal.level")}</th><th>{t("cal.delay")}</th><th><span className="sr-only">{t("cal.noise")}</span></th></tr></thead>
          <tbody>{order.map((ch) => (
            <tr key={ch} className={now === ch ? "is-active" : undefined}>
              <th scope="row"><span className="ch-code">{ch}</span> {t(`ch.${ch}`)}</th>
              <td><label className="cal-ctl"><span className="sr-only">{t("cal.level_of", { ch: t(`ch.${ch}`) })}</span>
                <input type="range" min={-GAIN} max={GAIN} step={0.5} value={trims[ch].gain_db} onChange={(e) => set(ch, "gain_db", Number(e.target.value))}
                  aria-valuetext={`${fmt(trims[ch].gain_db)} dB`} />
                <output>{fmt(trims[ch].gain_db)} dB</output></label></td>
              <td><label className="cal-ctl"><span className="sr-only">{t("cal.delay_of", { ch: t(`ch.${ch}`) })}</span>
                <input type="range" min={0} max={DELAY} step={0.5} value={trims[ch].delay_ms} onChange={(e) => set(ch, "delay_ms", Number(e.target.value))}
                  aria-valuetext={`${trims[ch].delay_ms} ms`} />
                <output>{trims[ch].delay_ms} ms{trims[ch].delay_ms ? <span className="hint"> ≈ {Math.round(trims[ch].delay_ms * 34.3)} {t("cal.cm")}</span> : null}</output></label></td>
              <td><button type="button" className="btn ghost small" disabled={!!now} onClick={() => play([ch])}
                aria-label={t("cal.noise_in", { ch: t(`ch.${ch}`) })}>{t("cal.noise")}</button></td>
            </tr>))}</tbody>
        </table></div>
      </div>
      <div className="row">
        {now ? <button type="button" className="btn ghost" onClick={() => { stop.current = true; }}>{t("cal.stop")}</button>
          : <button type="button" className="btn ghost" onClick={() => play(order)}>{t("cal.all")}</button>}
        <button type="button" className="btn" disabled={act.pending || !dirty} onClick={() => save(trims)}>{t("cal.save")}</button>
        <button type="button" className="btn ghost" disabled={act.pending || order.every((ch) => !trims[ch].gain_db && !trims[ch].delay_ms)}
          onClick={() => { const z = Object.fromEntries(order.map((ch) => [ch, { gain_db: 0, delay_ms: 0 }])); setTrims(z); save({}); }}>{t("cal.reset")}</button>
        <span role="status" className="hint">{now ? t("player.speaker_now", { ch: t(`ch.${now}`) }) : saved ? t("cal.saved", { layout: t(`layout.${layout}`) }) : dirty ? t("cal.unsaved") : ""}</span>
      </div>
      <ErrorBox error={act.error} />
    </section>
  );
}
