"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, Loading, Price, RequireAuth, useAction, useLoad } from "@/components/ui";
import { api } from "@/lib/api";

function MockCheckout() {
  const { ref } = useParams<{ ref: string }>();
  const { t, locale } = useApp();
  const router = useRouter();
  const { data: c, error, loading, reload } = useLoad<any>(`/mock-checkout/${ref}`);
  const [result, setResult] = useState<string | null>(null);
  const act = useAction();
  if (loading && !c) return <Loading />;
  if (error) return <ErrorBox error={error} onRetry={reload} />;
  const back = c.context === "hardware" ? `/${locale}/orders/${c.order_id}` : `/${locale}/library`;
  return (
    <div className="stack" style={{ maxWidth: 560 }}>
      <div className="notice test"><strong>{t("mock.title")}</strong><br />{t("mock.explain")}</div>
      <div className="panel stack">
        <h1 style={{ fontSize: "1.5rem" }}>{c.context === "hardware" ? t("mock.hardware", { order: c.order }) : c.app?.title}</h1>
        {c.kind === "subscription" && <p className="muted">{t(`mock.sub_${c.interval}`)}</p>}
        <div className="player-time"><Price minor={c.amount_minor} currency={c.currency} /></div>
        {c.status !== "created" || result ? (
          <div className="stack">
            <div className={`notice ${result === "failed" || c.status === "failed" ? "bad" : ""}`}>{t(`mock.result_${result ?? c.status}`)}</div>
            <button className="btn" onClick={() => router.push(back)}>{t("mock.continue")}</button>
          </div>
        ) : (
          <div className="row">
            <button className="btn" disabled={act.pending} onClick={async () => {
              const r = await act.run(() => api(`/mock-checkout/${ref}/complete`, { body: { outcome: "success" } }));
              if (r) setResult(r.status);
            }}>{t("mock.pay")}</button>
            <button className="btn ghost" disabled={act.pending} onClick={async () => {
              const r = await act.run(() => api(`/mock-checkout/${ref}/complete`, { body: { outcome: "fail" } }));
              if (r) setResult(r.status);
            }}>{t("mock.decline")}</button>
          </div>
        )}
        <ErrorBox error={act.error} />
      </div>
    </div>
  );
}

export default function MockCheckoutPage() {
  return <div className="section"><RequireAuth><MockCheckout /></RequireAuth></div>;
}
