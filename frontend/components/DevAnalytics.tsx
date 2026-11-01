"use client";

import { useState } from "react";
import { date } from "@/lib/i18n";
import { useApp } from "./Providers";
import { ErrorBox, Loading, useLoad } from "./ui";

/** NAP-027: developer analytics — funnel, weekly trend, retention. Small groups arrive as null and show as "hidden". */

type Report = {
  min_group: number;
  funnel: { days: number; steps: Record<string, number | null>; views_unavailable: boolean;
    rates: Record<string, number | null> };
  weekly: { week: string; views: number | null; started: number | null; completed: number | null; suppressed: boolean }[];
  retention: { week: string; size: number | null; suppressed: boolean; returned: Record<string, number> }[];
};

const STEPS = ["views", "access", "started", "completed"] as const;
const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

function Funnel({ r }: { r: Report }) {
  const { t, locale } = useApp();
  const vals = STEPS.map((s) => r.funnel.steps[s]);
  const max = Math.max(1, ...vals.map((v) => v ?? 0));
  const nf = new Intl.NumberFormat(locale);
  return (
    <ol className="funnel" aria-label={t("an.funnel")}>
      {STEPS.map((s, i) => {
        const v = vals[i];
        const hidden = v == null;
        return (
          <li key={s}>
            <span className="funnel-label">{t(`an.step_${s}`)}</span>
            <span className="funnel-track" aria-hidden="true">
              {!hidden && v! > 0 && <span className="funnel-bar" style={{ width: `${Math.max(1, (v! / max) * 100)}%` }} />}
            </span>
            <span className="funnel-value">
              {hidden ? (s === "views" && r.funnel.views_unavailable ? t("an.unavailable") : t("an.hidden", { n: r.min_group })) : nf.format(v!)}
              {i > 0 && !hidden && vals[i - 1] ? <span className="hint"> · {pct(v! / vals[i - 1]!)}</span> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Weekly({ r }: { r: Report }) {
  const { t, locale } = useApp();
  const [hover, setHover] = useState<number | null>(null);
  const W = 960, H = 220, PAD_B = 24, PAD_T = 8;
  const n = r.weekly.length;
  const max = Math.max(1, ...r.weekly.map((w) => w.started ?? 0));
  const slot = W / n, bw = Math.min(28, slot * 0.6);
  const y = (v: number) => H - PAD_B - (v / max) * (H - PAD_B - PAD_T);
  const label = (w: Report["weekly"][number]) => w.suppressed ? t("an.hidden", { n: r.min_group })
    : t("an.week_tip", { started: w.started ?? 0, completed: w.completed ?? 0, views: w.views ?? "—" });
  const cur = hover != null ? r.weekly[hover] : null;
  return (
    <figure className="stack chart">
      <figcaption className="row">
        <strong>{t("an.weekly")}</strong>
        <span className="legend"><i className="sw sw-started" />{t("an.started_sessions")}</span>
        <span className="legend"><i className="sw sw-completed" />{t("an.completed_sessions")}</span>
      </figcaption>
      <div className="hint chart-readout" aria-live="polite">{cur ? `${t("an.week_of", { date: date(cur.week, locale) })}: ${label(cur)}` : t("an.hover_hint")}</div>
      {r.weekly.every((w) => !w.started && !w.suppressed) && <p className="hint">{t("an.no_sessions")}</p>}
      <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" role="img" aria-label={t("an.weekly")} onMouseLeave={() => setHover(null)}>
        <line x1="0" x2={W} y1={H - PAD_B} y2={H - PAD_B} className="axis" />
        {r.weekly.map((w, i) => {
          const cx = i * slot + slot / 2;
          return (
            <g key={w.week}>
              {w.suppressed ? <rect x={cx - bw / 2} y={H - PAD_B - 6} width={bw} height={4} rx={2} className="bar-hidden" />
                : <>
                  {(w.started ?? 0) > 0 && <rect x={cx - bw / 2} y={y(w.started!)} width={bw} height={H - PAD_B - y(w.started!)} rx={4} className="bar-started" />}
                  {(w.completed ?? 0) > 0 && <rect x={cx - bw / 2 + 3} y={y(w.completed!)} width={bw - 6} height={H - PAD_B - y(w.completed!)} rx={3} className="bar-completed" />}
                </>}
              {(i % 2 === n % 2 || i === n - 1) && <text x={cx} y={H - 6} textAnchor="middle" className="tick">{new Date(w.week).toLocaleDateString(locale, { day: "numeric", month: "short" })}</text>}
              <rect x={i * slot} y={0} width={slot} height={H} fill="transparent" onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)}>
                <title>{label(w)}</title>
              </rect>
            </g>
          );
        })}
      </svg>
      <details><summary>{t("an.as_table")}</summary>
        <div className="scroll-x"><table><thead><tr><th>{t("an.week")}</th><th>{t("an.step_views")}</th><th>{t("an.started_sessions")}</th><th>{t("an.completed_sessions")}</th></tr></thead>
          <tbody>{r.weekly.map((w) => <tr key={w.week}><td>{date(w.week, locale)}</td><td>{w.views ?? "—"}</td>
            <td>{w.suppressed ? t("an.hidden", { n: r.min_group }) : w.started}</td><td>{w.suppressed ? "" : w.completed}</td></tr>)}</tbody></table></div>
      </details>
    </figure>
  );
}

function Retention({ r }: { r: Report }) {
  const { t, locale } = useApp();
  const rows = r.retention.filter((c) => c.size != null || c.suppressed);
  return (
    <section className="stack">
      <strong>{t("an.retention")}</strong>
      <p className="hint">{t("an.retention_hint")}</p>
      {!rows.length ? <p className="hint">{t("an.no_cohorts")}</p> : (
        <div className="scroll-x"><table className="retention"><thead><tr><th>{t("an.cohort")}</th><th>{t("an.cohort_size")}</th>
          {[1, 2, 3, 4].map((k) => <th key={k}>{t("an.week_n", { n: k })}</th>)}</tr></thead>
          <tbody>{rows.map((c) => <tr key={c.week}><td>{date(c.week, locale)}</td>
            <td>{c.suppressed ? t("an.hidden", { n: r.min_group }) : c.size}</td>
            {[1, 2, 3, 4].map((k) => { const v = c.returned[String(k)]; return <td key={k} className="ret-cell" style={v != null ? { background: `color-mix(in srgb, var(--left) ${Math.round(v * 70)}%, transparent)`, color: v > 0.5 ? "#fff" : undefined } : undefined}>{v == null ? "" : pct(v)}</td>; })}
          </tr>)}</tbody></table></div>)}
    </section>
  );
}

export function AppAnalytics({ appId }: { appId: string }) {
  const { t } = useApp();
  const [days, setDays] = useState(30);
  const { data, error, loading } = useLoad<Report>(`/developer/analytics/apps/${appId}?days=${days}`);
  return (
    <div className="stack">
      <div className="tabs" role="tablist" aria-label={t("an.period")}>
        {[7, 30, 90].map((d) => <button key={d} role="tab" aria-selected={days === d} onClick={() => setDays(d)}>{t("an.days", { n: d })}</button>)}
      </div>
      {error ? <ErrorBox error={error} /> : loading && !data ? <Loading /> : data && <>
        <Funnel r={data} />
        <Weekly r={data} />
        <Retention r={data} />
      </>}
    </div>
  );
}
