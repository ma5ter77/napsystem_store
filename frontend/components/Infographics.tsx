"use client";
// NAP-094: infographics — the DSP profile's carriers over time, the conversion QA as meters with threshold zones, and
// the equipment an app supports as icons. Colours follow the brand: left carrier --left, right carrier --right;
// status zones use --ok / --warn / --bad and always come with a text label. Every chart has a text alternative.

import { useId, useState } from "react";
import { useApp } from "@/components/Providers";
import { duration } from "@/lib/i18n";

export type Signal = { carrier_center_hz: number; beat_hz: number; beat_curve?: number[][] };

const fmt = (n: number, d = 1) => (Math.round(n * 10 ** d) / 10 ** d).toString();

/** Beat (difference) at time t: linear between curve points, held before the first and after the last. */
export function beatAt(s: Signal, t: number): number {
  const c = s.beat_curve ?? [];
  if (c.length < 2) return s.beat_hz;
  if (t <= c[0][0]) return c[0][1];
  for (let i = 1; i < c.length; i++) {
    if (t <= c[i][0]) { const [t0, b0] = c[i - 1], [t1, b1] = c[i]; return b0 + ((b1 - b0) * (t - t0)) / (t1 - t0); }
  }
  return c[c.length - 1][1];
}

/** Carriers L and R and their difference over time (two charts: Hz of a carrier and Hz of a difference differ in scale). */
export function SignalChart({ signal, durationSeconds }: { signal: Signal; durationSeconds?: number }) {
  const { t, locale } = useApp();
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const curve = signal.beat_curve ?? [];
  const end = Math.max(durationSeconds || 60, curve.length ? curve[curve.length - 1][0] * 1.1 : 0);
  const knots = [0, ...curve.map((p) => p[0]).filter((x) => x > 0 && x < end), end];
  const pts = knots.map((x) => ({ t: x, b: beatAt(signal, x) })).map((p) => ({ ...p, l: signal.carrier_center_hz - p.b / 2, r: signal.carrier_center_hz + p.b / 2 }));
  const W = 640, H = 170, HB = 110, PL = 52, PR = 74, PT = 10, PB = 24;
  const x = (s: number) => PL + (s / end) * (W - PL - PR);
  const lo = Math.min(...pts.map((p) => p.l)), hi = Math.max(...pts.map((p) => p.r));
  const pad = Math.max(2, (hi - lo) * 0.25);
  const y = (hz: number) => PT + (1 - (hz - (lo - pad)) / (hi + pad - (lo - pad))) * (H - PT - PB);
  const bMax = Math.max(...pts.map((p) => p.b)) * 1.25 || 1;
  const yb = (hz: number) => PT + (1 - hz / bMax) * (HB - PT - PB);
  const path = (k: "l" | "r", yy: (v: number) => number) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${yy(p[k]).toFixed(1)}`).join(" ");
  const bPath = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${yb(p.b).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const at = hover != null ? { t: hover, b: beatAt(signal, hover) } : null;
  const ticks = [0, end / 2, end];
  const constant = curve.length < 2;
  const summary = constant
    ? t("viz.signal_const", { l: fmt(last.l), r: fmt(last.r), b: fmt(last.b) })
    : t("viz.signal_curve", { from: fmt(pts[0].b), to: fmt(last.b), points: curve.length });
  const move = (e: React.PointerEvent<SVGRectElement>) => {
    const box = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const sx = ((e.clientX - box.left) / box.width) * W;
    setHover(Math.max(0, Math.min(end, ((sx - PL) / (W - PL - PR)) * end)));
  };
  const cross = at && <line x1={x(at.t)} x2={x(at.t)} y1={PT} y2={H - PB} className="crosshair" />;
  return (
    <figure className="stack chart viz" data-testid="signal-chart">
      <figcaption className="row">
        <strong>{t("viz.signal_title")}</strong>
        <span className="legend"><i className="sw sw-l" />{t("viz.left")}</span>
        <span className="legend"><i className="sw sw-r" />{t("viz.right")}</span>
      </figcaption>
      <p className="hint">{summary}</p>
      <div className="hint chart-readout" aria-live="polite">{at
        ? t("viz.signal_at", { time: duration(at.t, locale), l: fmt(signal.carrier_center_hz - at.b / 2, 2), r: fmt(signal.carrier_center_hz + at.b / 2, 2), b: fmt(at.b, 2) })
        : t("viz.hover_hint")}</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" role="img" aria-labelledby={`${id}-s`} onPointerLeave={() => setHover(null)}>
        <title id={`${id}-s`}>{summary}</title>
        {[lo, hi].map((hz) => <g key={hz}><line x1={PL} x2={W - PR} y1={y(hz)} y2={y(hz)} className="grid" />
          <text x={PL - 6} y={y(hz) + 4} textAnchor="end" className="tick">{fmt(hz)}</text></g>)}
        <line x1={PL} x2={W - PR} y1={H - PB} y2={H - PB} className="axis" />
        {ticks.map((s) => <text key={s} x={x(s)} y={H - 6} textAnchor="middle" className="tick">{duration(s, locale)}</text>)}
        <path d={path("l", y)} className="line-l" />
        <path d={path("r", y)} className="line-r" />
        <text x={W - PR + 6} y={y(last.l) + 4} className="tick label-l">L {fmt(last.l)}</text>
        <text x={W - PR + 6} y={y(last.r) + 4} className="tick label-r">R {fmt(last.r)}</text>
        {cross}
        <rect x={PL} y={0} width={W - PL - PR} height={H} fill="transparent" onPointerMove={move} />
      </svg>
      <svg viewBox={`0 0 ${W} ${HB}`} className="chart-svg" role="img" aria-label={t("viz.beat_title")} onPointerLeave={() => setHover(null)}>
        <text x={PL} y={PT + 2} className="tick">{t("viz.beat_title")}</text>
        <line x1={PL} x2={W - PR} y1={HB - PB} y2={HB - PB} className="axis" />
        <text x={PL - 6} y={HB - PB + 4} textAnchor="end" className="tick">0</text>
        <path d={bPath} className="line-b" />
        {!constant && curve.map(([s, b]) => s <= end && <circle key={s} cx={x(s)} cy={yb(b)} r={4} className="dot-b"><title>{`${duration(s, locale)}: ${fmt(b, 2)} Hz`}</title></circle>)}
        <text x={W - PR + 6} y={yb(last.b) + 4} className="tick">Δ {fmt(last.b)}</text>
        {at && <line x1={x(at.t)} x2={x(at.t)} y1={PT} y2={HB - PB} className="crosshair" />}
        <rect x={PL} y={0} width={W - PL - PR} height={HB} fill="transparent" onPointerMove={move} />
      </svg>
      <details><summary>{t("an.as_table")}</summary>
        <div className="scroll-x"><table><thead><tr><th>{t("viz.time")}</th><th>{t("viz.left")}</th><th>{t("viz.right")}</th><th>{t("viz.beat")}</th></tr></thead>
          <tbody>{pts.map((p) => <tr key={p.t}><td>{duration(p.t, locale)}</td><td>{fmt(p.l, 2)} Hz</td><td>{fmt(p.r, 2)} Hz</td><td>{fmt(p.b, 2)} Hz</td></tr>)}</tbody></table></div>
      </details>
      <p className="hint">{t("viz.signal_note")}</p>
    </figure>
  );
}

type Zone = { to: number; cls: "ok" | "warn" | "bad" };

function Meter({ label, value, min, max, unit, zones, from, status, statusText }: {
  label: string; value: number; min: number; max: number; unit: string; zones?: Zone[]; from?: number;
  status?: "ok" | "warn" | "bad"; statusText?: string;
}) {
  const pos = (v: number) => `${(Math.max(min, Math.min(max, v)) - min) / (max - min) * 100}%`;
  let start = min;
  return (
    <div className="meter stack" role="group" aria-label={label}>
      <div className="spread"><span>{label}</span><strong>{fmt(value)} {unit}</strong></div>
      <div className="meter-track" aria-hidden="true">
        {(zones ?? []).map((z) => { const s = start; start = z.to; return <i key={z.to} className={`meter-zone z-${z.cls}`} style={{ left: pos(s), width: `calc(${pos(z.to)} - ${pos(s)})` }} />; })}
        {from !== undefined && <i className="meter-from" style={{ left: pos(from) }} />}
        <i className="meter-mark" style={{ left: pos(value) }} />
      </div>
      <div className="spread hint" aria-hidden="true"><span>{min} {unit}</span><span>{max} {unit}</span></div>
      {status && statusText && <p className={`meter-status s-${status}`}><span aria-hidden="true">{status === "ok" ? "✓" : status === "warn" ? "!" : "✕"}</span> {statusText}</p>}
    </div>
  );
}

/** QA of a conversion as meters: true peak against the ceiling (zones), loudness source → result. */
export function QaMeters({ levels }: { levels: any }) {
  const { t } = useApp();
  const tp = Number(levels?.true_peak_master_dbtp), ceil = Number(levels?.true_peak_ceiling_dbtp), tol = Number(levels?.true_peak_tolerance_db ?? 0);
  const lo = Number(levels?.output_integrated_lufs), li = Number(levels?.source_integrated_lufs);
  const hasTp = Number.isFinite(tp) && Number.isFinite(ceil);
  const status = !hasTp ? undefined : tp <= ceil - tol + 1e-9 ? "ok" : tp <= ceil + 1e-9 ? "warn" : "bad";
  return (
    <div className="stack viz" data-testid="qa-meters">
      {hasTp && <Meter label={t("viz.tp")} value={tp} min={Math.min(-24, Math.floor(tp))} max={1} unit="dBTP"
        zones={[{ to: ceil - tol, cls: "ok" }, { to: ceil, cls: "warn" }, { to: 1, cls: "bad" }]}
        status={status} statusText={t(`viz.tp_${status}`, { c: ceil })} />}
      {Number.isFinite(lo) && <Meter label={t("viz.lufs")} value={lo} min={Math.min(-40, Math.floor(Math.min(lo, Number.isFinite(li) ? li : lo)))} max={-6} unit="LUFS" from={Number.isFinite(li) ? li : undefined} />}
      {Number.isFinite(lo) && <p className="hint">{Number.isFinite(li) ? t("viz.lufs_change", { from: fmt(li), to: fmt(lo) }) : t("viz.lufs_only", { to: fmt(lo) })}</p>}
    </div>
  );
}

const ICONS: Record<string, React.ReactNode> = {
  headphones: <path d="M6 22v-6a10 10 0 0 1 20 0v6M6 20h4v8H6zM22 20h4v8h-4z" />,
  acoustic_3_0: <><rect x="3" y="9" width="7" height="14" rx="1.5" /><rect x="12.5" y="12" width="7" height="8" rx="1.5" /><rect x="22" y="9" width="7" height="14" rx="1.5" /></>,
  surround_5_1: <><path d="M9 5h14v15H9zM7 20h18v4H7zM10 24v4M22 24v4" /><circle cx="16" cy="12" r="2.5" /></>,
  surround_7_1: <><path d="M9 5h14v15H9zM7 20h18v4H7zM10 24v4M22 24v4" /><circle cx="13" cy="10" r="1.8" /><circle cx="19" cy="10" r="1.8" /><circle cx="16" cy="15" r="1.8" /></>,
};

/** The equipment an app has versions for, as icons with names (the names carry the meaning, not the pictures). */
export function EquipmentIcons({ layouts, separation }: { layouts: string[]; separation?: boolean }) {
  const { t } = useApp();
  return (
    <ul className="equip-icons" data-testid="equipment-icons">
      {layouts.map((l) => (
        <li key={l}>
          <svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round">{ICONS[l]}</svg>
          <span>{t(`layout.${l}`)}</span>
          {l === "headphones" && separation && <span className="hint">{t("viz.separation")}</span>}
        </li>
      ))}
    </ul>
  );
}
