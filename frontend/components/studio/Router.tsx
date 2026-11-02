"use client";

// Channel router (spec v2.1 M2): the author's own output profiles. Rows are output channels in file order,
// columns are logical buses; a cell is a gain in dB (empty = not routed). Per channel: mute, polarity, delay.
// Every save is a new immutable version, so earlier exports keep the routing they were made with.
import { useMemo, useState } from "react";
import { useApp } from "@/components/Providers";
import { Empty, ErrorBox, Loading, useAction, useLoad } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { BUSES } from "./model";
import type { Profile } from "./Render";

const LAYOUTS: Record<string, string[]> = {
  stereo: ["FL", "FR"], "3.0": ["FL", "FR", "FC"], "5.1(side)": ["FL", "FR", "FC", "LFE", "SL", "SR"],
  "7.1": ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
};
const BASE_FOR_LAYOUT: Record<string, string> = { stereo: "HEADPHONES_2_0", "3.0": "NAP_ACOUSTIC_3_0", "5.1(side)": "NAP_CHAIR_5_1", "7.1": "NAP_PRO_7_1" };
const MAX_DB = 12;

type Opt = { mute: boolean; invert: boolean; delay_ms: number };
type Draft = { key?: string; name: string; layout: string; cells: string[][]; options: Opt[]; note: string };

export function toDb(g: number): string {
  const db = 20 * Math.log10(g);
  return Math.abs(db) < 1e-9 ? "0" : String(+db.toFixed(2));
}
export function fromDb(text: string): number | null {
  const v = Number(text.replace(",", "."));
  return text.trim() === "" || !Number.isFinite(v) ? null : Math.pow(10, v / 20);
}

function draftFrom(p: Profile, copy: boolean, t: (k: string, q?: any) => string): Draft {
  const opts = p.options;
  return {
    key: copy ? undefined : p.key,
    name: copy ? t("studio.router_copy_of", { name: p.name }) : p.name,
    layout: p.layout,
    cells: p.matrix.map((row) => BUSES.map((b) => (b in row ? toDb(row[b]) : ""))),
    options: p.channels.map((_, i) => ({ mute: !!opts?.[i]?.mute, invert: !!opts?.[i]?.invert, delay_ms: Number(opts?.[i]?.delay_ms ?? 0) })),
    note: copy ? "" : p.note,
  };
}

function problems(d: Draft, t: (k: string, q?: any) => string): string[] {
  const out: string[] = [];
  if (!d.name.trim()) out.push(t("studio.router_need_name"));
  d.cells.forEach((row, i) => row.forEach((c, j) => {
    if (c.trim() === "") return;
    const v = Number(c.replace(",", "."));
    if (!Number.isFinite(v)) out.push(t("studio.router_bad_gain", { ch: LAYOUTS[d.layout][i], bus: t(`studio.bus.${BUSES[j]}`) }));
    else if (v > MAX_DB) out.push(t("studio.router_too_loud", { ch: LAYOUTS[d.layout][i], bus: t(`studio.bus.${BUSES[j]}`) }));
  }));
  d.options.forEach((o, i) => { if (!(o.delay_ms >= 0 && o.delay_ms <= 100)) out.push(t("studio.router_bad_delay", { ch: LAYOUTS[d.layout][i] })); });
  return out;
}

function Editor({ draft, builtins, onSaved, onCancel }: { draft: Draft; builtins: Profile[]; onSaved: () => void; onCancel: () => void }) {
  const { t } = useApp();
  const [d, setD] = useState<Draft>(draft);
  const act = useAction();
  const chans = LAYOUTS[d.layout];
  const issues = useMemo(() => problems(d, t), [d, t]);
  const setCell = (i: number, j: number, v: string) => setD({ ...d, cells: d.cells.map((r, a) => (a === i ? r.map((c, b) => (b === j ? v : c)) : r)) });
  const setOpt = (i: number, patch: Partial<Opt>) => setD({ ...d, options: d.options.map((o, a) => (a === i ? { ...o, ...patch } : o)) });
  const changeLayout = (layout: string) => {
    const base = builtins.find((b) => b.key === BASE_FOR_LAYOUT[layout]);
    if (base) setD({ ...draftFrom(base, true, t), key: d.key, name: d.name, note: d.note, layout });
  };
  const save = () => act.run(async () => {
    const body = {
      name: d.name.trim(), layout: d.layout, note: d.note,
      matrix: d.cells.map((row) => Object.fromEntries(row.flatMap((c, j) => { const g = fromDb(c); return g === null ? [] : [[BUSES[j], g]]; }))),
      options: d.options.map((o) => ({ mute: o.mute, invert: o.invert, delay_ms: Number(o.delay_ms) || 0 })),
    };
    await api(d.key ? `/audio/routing-profiles/${d.key}` : "/audio/routing-profiles", { method: d.key ? "PUT" : "POST", body });
    onSaved();
  });
  const fieldErrors = act.error instanceof ApiError ? (act.error.details?.errors as { path: string; message: string }[] | undefined) : undefined;
  return (
    <div className="panel stack studio-router-edit">
      <div className="form wide">
        <label className="field">{t("studio.router_name")}<input value={d.name} maxLength={80} onChange={(e) => setD({ ...d, name: e.target.value })} /></label>
        <label className="field">{t("studio.router_layout")}<select value={d.layout} onChange={(e) => changeLayout(e.target.value)}>
          {Object.keys(LAYOUTS).map((l) => <option key={l} value={l}>{l} — {LAYOUTS[l].join(" ")}</option>)}</select>
          <span className="hint">{t("studio.router_layout_hint")}</span></label>
      </div>
      <div className="studio-router-scroll">
        <table className="studio-router">
          <caption className="sr-only">{t("studio.router_caption")}</caption>
          <thead><tr><th scope="col">{t("studio.router_output")}</th>
            {BUSES.map((b) => <th key={b} scope="col">{t(`studio.bus.${b}`)}</th>)}
            <th scope="col">{t("studio.router_mute")}</th><th scope="col">{t("studio.router_invert")}</th><th scope="col">{t("studio.router_delay")}</th></tr></thead>
          <tbody>{chans.map((ch, i) => (
            <tr key={ch} className={d.options[i]?.mute ? "is-muted" : undefined}>
              <th scope="row">{ch}</th>
              {BUSES.map((b, j) => (
                <td key={b}><input aria-label={`${ch} ← ${t(`studio.bus.${b}`)}, dB`} inputMode="decimal" placeholder="—"
                  className={d.cells[i][j] ? "is-on" : undefined} value={d.cells[i][j]} onChange={(e) => setCell(i, j, e.target.value)} /></td>
              ))}
              <td><input type="checkbox" aria-label={`${ch}: ${t("studio.router_mute")}`} checked={d.options[i].mute} onChange={(e) => setOpt(i, { mute: e.target.checked })} /></td>
              <td><input type="checkbox" aria-label={`${ch}: ${t("studio.router_invert")}`} checked={d.options[i].invert} onChange={(e) => setOpt(i, { invert: e.target.checked })} /></td>
              <td><input aria-label={`${ch}: ${t("studio.router_delay")}`} inputMode="decimal" className="studio-router-delay"
                value={String(d.options[i].delay_ms)} onChange={(e) => setOpt(i, { delay_ms: Number(e.target.value.replace(",", ".")) })} /></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <p className="hint">{t("studio.router_cells_hint")}</p>
      <label className="field">{t("studio.router_note")}<input value={d.note} maxLength={500} onChange={(e) => setD({ ...d, note: e.target.value })} /></label>
      {issues.length > 0 && <ul className="notice warn studio-router-issues">{issues.map((x) => <li key={x}>{x}</li>)}</ul>}
      <ErrorBox error={act.error} />
      {fieldErrors && <ul className="hint">{fieldErrors.map((e) => <li key={e.path + e.message}><code>{e.path}</code>: {e.message}</li>)}</ul>}
      <div className="studio-row">
        <button className="btn" disabled={act.pending || issues.length > 0} onClick={save}>{d.key ? t("studio.router_save_version") : t("studio.router_create")}</button>
        <button className="btn ghost" onClick={onCancel}>{t("studio.router_cancel")}</button>
      </div>
    </div>
  );
}

export function RouterPanel() {
  const { t } = useApp();
  const { data, error, loading, reload } = useLoad<{ items: Profile[] }>("/audio/routing-profiles");
  const [edit, setEdit] = useState<Draft | null>(null);
  const act = useAction();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const builtins = data!.items.filter((p) => p.builtin);
  const own = data!.items.filter((p) => !p.builtin);
  return (
    <section className="stack" aria-labelledby="router-h">
      <div className="spread"><h2 id="router-h">{t("studio.router")}</h2>
        {!edit && <label className="studio-inline">{t("studio.router_new_from")}
          <select value="" onChange={(e) => { const b = builtins.find((x) => x.key === e.target.value); if (b) setEdit(draftFrom(b, true, t)); }}>
            <option value="">{t("studio.router_pick_base")}</option>
            {builtins.map((b) => <option key={b.key} value={b.key}>{b.name}</option>)}</select></label>}
      </div>
      <p className="hint">{t("studio.router_lead")}</p>
      {edit && <Editor key={edit.key ?? "new"} draft={edit} builtins={builtins} onCancel={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
      {!own.length ? <Empty>{t("studio.router_empty")}</Empty> : (
        <ul className="studio-sources">
          {own.map((p) => (
            <li key={p.key}>
              <div className="studio-source-main">
                <strong>{p.name}</strong>
                <span className="muted">{p.layout} · {t("studio.router_version", { n: p.version })}</span>
              </div>
              <span className="studio-row">
                <button className="btn small ghost" onClick={() => setEdit(draftFrom(p, false, t))}>{t("studio.router_edit")}</button>
                <button className="btn small ghost" disabled={act.pending} onClick={() => act.run(async () => { await api(`/audio/routing-profiles/${p.key}`, { method: "DELETE" }); reload(); })}>{t("studio.router_archive")}</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <ErrorBox error={act.error} />
    </section>
  );
}
