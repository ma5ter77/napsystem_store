"use client";

// NAP-093: a short explanation of a technical term (LUFS, true peak, carrier…), opened by click, Enter or Space and
// closed by Esc, a second click or a click elsewhere. The text lives in the dictionary under terms.<key>.
import { useEffect, useId, useRef, useState } from "react";
import { useApp } from "@/components/Providers";

export const TERMS = ["carrier", "beat", "separation", "lufs", "true_peak", "gain", "dbfs", "ramp"] as const;
export type TermKey = (typeof TERMS)[number];

export function Term({ k }: { k: TermKey }) {
  const { t } = useApp();
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); btn.current?.focus(); } };
    const onDown = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [open]);
  const name = t(`terms.${k}.name`);
  return (
    <span className="term" ref={box}>
      <button ref={btn} type="button" className="term-btn" aria-expanded={open} aria-controls={open ? id : undefined}
        aria-label={t("terms.what_is", { term: name })} title={t("terms.what_is", { term: name })}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(!open); }}>?</button>
      {/* rendered only while open, so the explanation does not mix into the page text */}
      {open && <span id={id} className="term-pop" role="note" data-testid={`term-${k}`}>
        <strong>{name}.</strong> {t(`terms.${k}.text`)}
      </span>}
    </span>
  );
}
