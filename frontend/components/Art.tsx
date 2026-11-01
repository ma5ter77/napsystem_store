/** NAP-040: a curated set of well-separated cover tones (no muddy dark reds). Each tone keeps white and the
 * steel/rose carrier lines readable (contrast ≥ 3:1 for the lines on the darkest point of the background). */
export const COVER_TONES: { bg: string; glow: string }[] = [
  // NAP-095: light, airy tints (design v2); the carriers stay the brand blue and rose (contrast ≥ 3:1 on every tint)
  { bg: "hsl(205 60% 95%)", glow: "hsl(200 70% 86%)" },  // ocean
  { bg: "hsl(165 40% 94%)", glow: "hsl(160 45% 84%)" },  // teal
  { bg: "hsl(250 50% 96%)", glow: "hsl(265 55% 88%)" },  // indigo-violet
  { bg: "hsl(40 70% 94%)", glow: "hsl(42 80% 84%)" },    // amber
  { bg: "hsl(220 18% 95%)", glow: "hsl(212 28% 86%)" },  // slate
  { bg: "hsl(320 45% 96%)", glow: "hsl(332 55% 88%)" },  // plum
  { bg: "hsl(120 30% 94%)", glow: "hsl(100 35% 84%)" },  // forest
  { bg: "hsl(20 60% 95%)", glow: "hsl(24 70% 86%)" },    // copper
];

/** Stable tone for an app on its own (detail page, player): its hue bucket. */
export function toneFor(hue: number | null | undefined) {
  return Math.floor((((hue ?? 210) % 360) + 360) % 360 / (360 / COVER_TONES.length)) % COVER_TONES.length;
}

/** Tones for a list in display order: each card keeps its own tone unless one of the previous `window` cards
 *  already has it — then it takes the nearest free tone, so neighbours in a row and in the row above differ. */
export function spreadTones(hues: (number | null | undefined)[], window = 4): number[] {
  const out: number[] = [];
  hues.forEach((h, i) => {
    const recent = new Set(out.slice(Math.max(0, i - window), i));
    let t = toneFor(h);
    for (let k = 0; k < COVER_TONES.length && recent.has(t); k++) t = (t + 3) % COVER_TONES.length;
    out.push(t);
  });
  return out;
}

/** Generated covers: two carriers (L steel, R rose) and the beat envelope they produce. No stock imagery. */
export function BeatCover({ hue = 210, tone, seed = "x", beats = 3, title }: { hue?: number; tone?: number; seed?: string; beats?: number; title?: string }) {
  const W = 400, H = 300;
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const f1 = 4 + (h % 4), f2 = f1 + beats * 0.35;
  const pts = (f: number, amp: number, phase: number) =>
    Array.from({ length: 161 }, (_, i) => {
      const x = (i / 160) * W;
      const y = H / 2 + amp * Math.sin((i / 160) * f * Math.PI * 2 + phase);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  const env = Array.from({ length: 161 }, (_, i) => {
    const x = (i / 160) * W;
    const e = Math.abs(Math.cos((i / 160) * (f2 - f1) * Math.PI));
    return `${x.toFixed(1)},${(H / 2 - 92 * e).toFixed(1)}`;
  }).join(" ");
  const c = COVER_TONES[tone ?? toneFor(hue)];
  // the glow sits in one of four corners (from the seed), so equal tones still look different
  const gx = (h >> 3) % 2 ? W * 0.85 : W * 0.15, gy = (h >> 4) % 2 ? H * 0.8 : H * 0.2;
  const gid = `g${h.toString(36)}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} {...(title ? { role: "img", "aria-label": title } : { "aria-hidden": true })} preserveAspectRatio="xMidYMid slice" data-tone={tone ?? toneFor(hue)}>
      <defs><radialGradient id={gid} cx={gx} cy={gy} r={W * 0.75} gradientUnits="userSpaceOnUse">
        <stop offset="0" stopColor={c.glow} stopOpacity=".75" /><stop offset="1" stopColor={c.glow} stopOpacity="0" /></radialGradient></defs>
      <rect width={W} height={H} fill={c.bg} />
      <rect width={W} height={H} fill={`url(#${gid})`} />
      <polyline points={env} fill="none" stroke="#fff" strokeOpacity=".7" strokeWidth="26" strokeLinecap="round" />
      <polyline points={pts(f1, 70, 0)} fill="none" stroke="#2f6fa3" strokeWidth="2.5" />
      <polyline points={pts(f2, 70, 0.6)} fill="none" stroke="#b03f6f" strokeWidth="2.5" />
    </svg>
  );
}

const SHAPES: Record<string, string> = {
  chairs: "M90 210 L150 90 Q160 70 185 75 L205 82 L180 150 L290 150 Q310 150 310 170 L310 210 Z M110 210 L100 250 M290 210 L300 250",
  headphones: "M120 190 Q120 90 200 90 Q280 90 280 190 M105 175 h35 v60 h-35 z M260 175 h35 v60 h-35 z",
  vibroacoustic: "M90 120 h220 v100 h-220 z M110 145 h180 M110 170 h180 M110 195 h180",
  controllers: "M120 120 h160 v90 h-160 z M150 165 a12 12 0 1 0 0.1 0 M250 150 h-40 M250 180 h-40",
  sensors: "M140 170 a60 30 0 1 0 120 0 a60 30 0 1 0 -120 0 M200 170 m-14 0 a14 14 0 1 0 28 0 a14 14 0 1 0 -28 0",
  power: "M130 130 h110 v80 h-110 z M240 150 h30 M240 190 h30 M130 170 q-40 0 -50 40",
  cables: "M90 200 q60 -120 110 -40 t110 -40 M90 200 h-8 M310 120 h8",
};

export function ProductArt({ category, hue = 30, title }: { category: string; hue?: number; title?: string }) {
  return (
    <svg viewBox="0 0 400 300" {...(title ? { role: "img", "aria-label": title } : { "aria-hidden": true })} preserveAspectRatio="xMidYMid slice">
      <rect width="400" height="300" fill={`hsl(${hue} 30% 95%)`} />
      <circle cx="320" cy="60" r="90" fill={`hsl(${hue} 40% 88%)`} />
      <path d={SHAPES[category] ?? SHAPES.controllers} fill="none" stroke={`hsl(${hue} 30% 24%)`} strokeWidth="7"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 30 18" aria-hidden="true">
      <path d="M1 9 Q5 1 9 9 T17 9 T25 9 T29 9" fill="none" stroke="var(--left)" strokeWidth="2.2" />
      <path d="M1 9 Q5.6 17 10 9 T19 9 T28 9" fill="none" stroke="var(--right)" strokeWidth="2.2" />
    </svg>
  );
}

/** NAP-025: the uploaded, moderated cover when the app has one; otherwise the generated beat cover. */
export function AppCover({ a, title, eager, tone }: { a: { cover_url?: string | null; cover_hue: number; slug: string; title?: string | null }; title?: string | null; eager?: boolean; tone?: number }) {
  if (a.cover_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={a.cover_url} alt={title ?? a.title ?? ""} loading={eager ? "eager" : "lazy"} decoding="async" width={1200} height={900} className="cover-img" />;
  }
  return <BeatCover hue={a.cover_hue} tone={tone} seed={a.slug} title={title ?? a.title ?? undefined} />;
}

// NAP-095: wide hero motif — the left and right carriers drifting apart; where they line up the beat swells.
export function HeroWaves() {
  const W = 1200, H = 420, N = 600;
  const fL = 9, fR = 10;  // cycles across the width: one beat in the middle of the picture
  const line = (f: number, phase: number) => Array.from({ length: N + 1 }, (_, i) => {
    const x = (i / N) * W;
    const env = 0.35 + 0.65 * Math.abs(Math.cos(Math.PI * (fR - fL) * (i / N) - Math.PI / 2));
    const y = H / 2 + 120 * env * Math.sin((i / N) * f * Math.PI * 2 + phase);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" role="presentation">
      <rect width={W} height={H} fill="var(--fog)" />
      <polyline points={line(fL, 0)} fill="none" stroke="var(--left)" strokeWidth="3" strokeLinecap="round" />
      <polyline points={line(fR, 0)} fill="none" stroke="var(--right)" strokeWidth="3" strokeLinecap="round" opacity=".9" />
      <text x="40" y={H - 36} fill="var(--left)" fontSize="22" fontWeight="600" fontFamily="var(--text)">L</text>
      <text x={W - 56} y={H - 36} fill="var(--right)" fontSize="22" fontWeight="600" fontFamily="var(--text)">R</text>
    </svg>
  );
}
