"use client";

// NAP-044: an unexpected failure while rendering a page — say so plainly and offer to try again.
import { useEffect } from "react";
import { useApp } from "@/components/Providers";
import { StatePanel } from "@/components/State";

export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const { t, locale } = useApp();
  useEffect(() => { console.error(error); }, [error]);
  return (
    <div className="section">
      <StatePanel kind="error" title={t("state.crash_title")}
        actions={<><button className="btn" onClick={reset}>{t("common.retry")}</button><a className="btn ghost" href={`/${locale}`}>{t("state.home")}</a></>}>
        {t("state.crash_text")}{error.digest ? ` ${t("common.request_id")}: ${error.digest}` : ""}
      </StatePanel>
    </div>
  );
}
