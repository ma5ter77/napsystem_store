"use client";
// NAP-092: one status component for every flow — a line of steps (mark + colour + text, readable by screen
// readers) and, optionally, the next thing that happens. Step logic lives in lib/steps.ts.
import type { Steps } from "@/lib/steps";
import { CheckIcon } from "./icons";
import { useApp } from "./Providers";

export function StatusTimeline({ value, ns, label, showNext = true }: { value: Steps; ns: string; label: string; showNext?: boolean }) {
  const { t } = useApp();
  return (
    <div>
      <ol className="timeline" aria-label={label}>
        {value.steps.map((s, i) => (
          <li key={s.key} className={s.state} aria-current={s.state === "current" || s.state === "problem" ? "step" : undefined}>
            <span className="dot" aria-hidden="true">{s.state === "done" ? <CheckIcon /> : s.state === "problem" ? "!" : i + 1}</span>
            {t(`steps.${ns}.${s.key}`)}
            <span className="sr-only"> — {t(`steps.state.${s.state}`)}</span>
          </li>
        ))}
      </ol>
      {showNext && <p className="next-step"><strong>{t("steps.next")}</strong>{t(`steps.${ns}_next.${value.next}`)}</p>}
    </div>
  );
}
