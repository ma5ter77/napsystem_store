"use client";
// NAP-092: status as a timeline (colour + mark + text) with the next step spelled out.
import { orderSteps } from "@/lib/orderSteps";
import { CheckIcon } from "./icons";
import { useApp } from "./Providers";

export function OrderTimeline({ order, showNext = true }: { order: any; showNext?: boolean }) {
  const { t } = useApp();
  const { steps, next } = orderSteps(order);
  return (
    <div>
      <ol className="timeline" aria-label={t("steps.order_progress")}>
        {steps.map((s, i) => (
          <li key={s.key} className={s.state} aria-current={s.state === "current" || s.state === "problem" ? "step" : undefined}>
            <span className="dot" aria-hidden="true">{s.state === "done" ? <CheckIcon /> : s.state === "problem" ? "!" : i + 1}</span>
            {t(`steps.order.${s.key}`)}
            <span className="sr-only"> — {t(`steps.state.${s.state}`)}</span>
          </li>
        ))}
      </ol>
      {showNext && <p className="next-step"><strong>{t("steps.next")}</strong>{t(`steps.order_next.${next}`)}</p>}
    </div>
  );
}
