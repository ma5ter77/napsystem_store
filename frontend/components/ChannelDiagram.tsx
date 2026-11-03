"use client";

// NAP-115: what plays where, for the author and the moderator (schematic, "for the picture").
// The NAP chair, seen from the front: the backrest is split into segments, each a separate membrane (driver) fixed
// to the backrest — 3 segments for 3.0, 5 for 5.1 and 7 for 7.1, plus the subwoofer in the seat. The front pair carries
// the binaural shift and wears the brand colours of the left and right carriers. Headphones: left and right (two
// carriers, the binaural beat is their difference) and the voice in front of the head.
// The channel picked in the table (or playing in the speaker test) is highlighted; own files are marked.
import { useApp } from "@/components/Providers";

export type DiagramLayout = "headphones" | "acoustic_3_0" | "surround_5_1" | "surround_7_1";

type Seg = { ch: string; x: number; y: number; w: number; h: number };

// backrest 60..240 wide, 70..250 tall; headrest above it; seat below
const SEGMENTS: Record<Exclude<DiagramLayout, "headphones">, Seg[]> = {
  acoustic_3_0: [
    { ch: "FC", x: 112, y: 22, w: 76, h: 40 },
    { ch: "FL", x: 64, y: 74, w: 84, h: 172 }, { ch: "FR", x: 152, y: 74, w: 84, h: 172 },
  ],
  surround_5_1: [
    { ch: "FC", x: 112, y: 22, w: 76, h: 40 },
    { ch: "FL", x: 64, y: 74, w: 84, h: 84 }, { ch: "FR", x: 152, y: 74, w: 84, h: 84 },
    { ch: "SL", x: 64, y: 162, w: 84, h: 84 }, { ch: "SR", x: 152, y: 162, w: 84, h: 84 },
    { ch: "LFE", x: 100, y: 268, w: 100, h: 40 },
  ],
  surround_7_1: [
    { ch: "FC", x: 112, y: 22, w: 76, h: 40 },
    { ch: "FL", x: 64, y: 74, w: 84, h: 55 }, { ch: "FR", x: 152, y: 74, w: 84, h: 55 },
    { ch: "SL", x: 64, y: 133, w: 84, h: 55 }, { ch: "SR", x: 152, y: 133, w: 84, h: 55 },
    { ch: "BL", x: 64, y: 192, w: 84, h: 55 }, { ch: "BR", x: 152, y: 192, w: 84, h: 55 },
    { ch: "LFE", x: 100, y: 268, w: 100, h: 40 },
  ],
};

export function diagramChannels(layout: DiagramLayout): string[] {
  return layout === "headphones" ? ["FL", "FR"] : SEGMENTS[layout].map((s) => s.ch);
}

function tone(ch: string) { return ch === "FL" ? " cd-left" : ch === "FR" ? " cd-right" : ""; }

export function ChannelDiagram({ layout, own = [], active, onPick, idPrefix = "ch" }: {
  layout: DiagramLayout; own?: string[]; active?: string | null; onPick?: (ch: string) => void; idPrefix?: string;
}) {
  const { t } = useApp();
  const chans = diagramChannels(layout);
  const summary = t(layout === "headphones" ? "diagram.label_hp" : "diagram.label",
    { layout: t(`layout.${layout}`), list: chans.map((c) => t(`ch.${c}`)).join(", ") });
  const pick = onPick ? (ch: string) => () => onPick(ch) : undefined;
  const cls = (ch: string) => `cd-seg${tone(ch)}${active === ch ? " is-active" : ""}${own.includes(ch) ? " is-own" : ""}`;
  return (
    <figure className="channel-diagram" data-testid={`diagram-${layout}`}>
      {layout === "headphones" ? (
        <svg viewBox="0 0 300 270" role="img" aria-label={summary}>
          {/* voice in front of the head */}
          <g className="cd-voice" aria-hidden="true">
            <path d="M150 18 a14 14 0 1 1 -0.1 0 Z" />
            <path d="M143 22 v14 M150 19 v20 M157 22 v14" />
            <text x="150" y="58" textAnchor="middle">{t("diagram.voice")}</text>
          </g>
          <g className="cd-head" aria-hidden="true">
            <ellipse cx="150" cy="150" rx="52" ry="62" />
            <path d="M92 132 Q150 46 208 132" className="cd-band" />
          </g>
          {/* the beat: the difference between the two carriers */}
          <path className="cd-beat" aria-hidden="true" d="M106 230 q11 -12 22 0 t22 0 t22 0 t22 0" />
          <text className="cd-beat-label" x="150" y="258" textAnchor="middle" aria-hidden="true">{t("diagram.beat")}</text>
          {[{ ch: "FL", x: 70 }, { ch: "FR", x: 206 }].map(({ ch, x }) => (
            <g key={ch} className={cls(ch)} data-ch={ch} onClick={pick?.(ch)} style={onPick ? { cursor: "pointer" } : undefined} aria-hidden="true">
              <rect x={x} y="118" width="24" height="64" rx="11" />
              <text x={x + 12} y="155" textAnchor="middle">{ch === "FL" ? "L" : "R"}</text>
            </g>
          ))}
        </svg>
      ) : (
        <svg viewBox="0 0 300 330" role="img" aria-label={summary}>
          <g className="cd-chair" aria-hidden="true">
            <rect x="104" y="16" width="92" height="52" rx="18" />{/* headrest */}
            <rect x="56" y="66" width="188" height="188" rx="26" />{/* backrest */}
            <rect x="30" y="150" width="22" height="120" rx="11" /><rect x="248" y="150" width="22" height="120" rx="11" />
            <rect x="66" y="258" width="168" height="58" rx="20" />{/* seat */}
          </g>
          {SEGMENTS[layout].map((s) => (
            <g key={s.ch} className={cls(s.ch)} data-ch={s.ch} onClick={pick?.(s.ch)} style={onPick ? { cursor: "pointer" } : undefined} aria-hidden="true">
              <rect x={s.x} y={s.y} width={s.w} height={s.h} rx="12" />
              {/* the membrane */}
              <circle className="cd-cone" cx={s.x + s.w / 2} cy={s.y + s.h / 2} r={Math.min(s.w, s.h) / 2 - 8} />
              <text x={s.x + s.w / 2} y={s.y + s.h / 2 + 4} textAnchor="middle">{s.ch}</text>
              {own.includes(s.ch) && <circle className="cd-own" cx={s.x + s.w - 8} cy={s.y + 8} r="5" />}
            </g>
          ))}
        </svg>
      )}
      <figcaption className="cd-legend">
        <span><i className="cd-key cd-left" />{t(layout === "headphones" ? "diagram.shift_hp" : "diagram.shift")}</span>
        {own.length > 0 && <span><i className="cd-key cd-ownkey" />{t("diagram.own")}</span>}
        <span className="hint">{t(layout === "headphones" ? "diagram.hp_note" : "diagram.chair_note")}</span>
      </figcaption>
      <ul className="sr-only">{chans.map((c) => <li key={c} id={`${idPrefix}-d-${c}`}>{c}: {t(`ch.${c}`)}{own.includes(c) ? ` — ${t("diagram.own")}` : ""}</li>)}</ul>
    </figure>
  );
}

/** Studio layout names -> store layouts. */
export const DIAGRAM_FOR_STUDIO: Record<string, DiagramLayout> = { stereo: "headphones", "3.0": "acoustic_3_0", "5.1(side)": "surround_5_1", "7.1": "surround_7_1" };
