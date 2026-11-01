/** Generated covers: two carriers (L steel, R rose) and the beat envelope they produce. No stock imagery. */
export function BeatCover({ hue = 210, seed = "x", beats = 3, title }: { hue?: number; seed?: string; beats?: number; title?: string }) {
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
  const bg = `hsl(${hue} 32% 22%)`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} {...(title ? { role: "img", "aria-label": title } : { "aria-hidden": true })} preserveAspectRatio="xMidYMid slice">
      <rect width={W} height={H} fill={bg} />
      <rect width={W} height={H} fill={`hsl(${(hue + 40) % 360} 40% 30%)`} opacity=".35" />
      <polyline points={env} fill="none" stroke="#fff" strokeOpacity=".22" strokeWidth="26" strokeLinecap="round" />
      <polyline points={pts(f1, 70, 0)} fill="none" stroke="#8cc6e0" strokeWidth="2.5" />
      <polyline points={pts(f2, 70, 0.6)} fill="none" stroke="#f0a5c4" strokeWidth="2.5" />
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
      <rect width="400" height="300" fill={`hsl(${hue} 22% 88%)`} />
      <circle cx="320" cy="60" r="90" fill={`hsl(${hue} 35% 80%)`} />
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
export function AppCover({ a, title, eager }: { a: { cover_url?: string | null; cover_hue: number; slug: string; title?: string | null }; title?: string | null; eager?: boolean }) {
  if (a.cover_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={a.cover_url} alt={title ?? a.title ?? ""} loading={eager ? "eager" : "lazy"} decoding="async" width={1200} height={900} className="cover-img" />;
  }
  return <BeatCover hue={a.cover_hue} seed={a.slug} title={title ?? a.title ?? undefined} />;
}
