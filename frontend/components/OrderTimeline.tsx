"use client";
// NAP-092: hardware order status as a timeline with the next step spelled out.
import { orderSteps } from "@/lib/orderSteps";
import { useApp } from "./Providers";
import { StatusTimeline } from "./StatusTimeline";

export function OrderTimeline({ order, showNext = true }: { order: any; showNext?: boolean }) {
  const { t } = useApp();
  return <StatusTimeline value={orderSteps(order)} ns="order" label={t("steps.order_progress")} showNext={showNext} />;
}
