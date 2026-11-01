"use client";

import { useState } from "react";
import { useApp } from "./Providers";

type Photo = { id: string; url: string; width?: number | null; height?: number | null };

/** NAP-047: product photo gallery — main photo, previous/next, thumbnails; arrow keys work on the main photo. */
export function ProductGallery({ photos, title }: { photos: Photo[]; title: string }) {
  const { t } = useApp();
  const [i, setI] = useState(0);
  const n = photos.length;
  const go = (k: number) => setI((k + n) % n);
  const cur = photos[i];
  return (
    <section className="gallery" aria-label={t("ph.gallery")}>
      <div className="gallery-main" tabIndex={0} onKeyDown={(e) => { if (e.key === "ArrowLeft") go(i - 1); if (e.key === "ArrowRight") go(i + 1); }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={cur.url} alt={n > 1 ? `${title} — ${t("ph.alt", { n: i + 1 })}` : title} width={cur.width ?? undefined} height={cur.height ?? undefined} decoding="async" />
        {n > 1 && <>
          <button type="button" className="gallery-nav prev" onClick={() => go(i - 1)} aria-label={t("ph.prev")}>‹</button>
          <button type="button" className="gallery-nav next" onClick={() => go(i + 1)} aria-label={t("ph.next")}>›</button>
          <span className="gallery-count" aria-live="polite">{i + 1} / {n}</span>
        </>}
      </div>
      {n > 1 && (
        <div className="gallery-thumbs">
          {photos.map((ph, k) => (
            <button key={ph.id} type="button" aria-current={k === i} aria-label={t("ph.show", { n: k + 1, total: n })} onClick={() => setI(k)}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={ph.url} alt="" loading="lazy" />
            </button>))}
        </div>)}
    </section>
  );
}
