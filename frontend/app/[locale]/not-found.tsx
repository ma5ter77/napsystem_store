"use client";

// NAP-044: localized "not found" inside the site layout (header, language switch, footer stay in place).
import Link from "next/link";
import { useApp } from "@/components/Providers";
import { StatePanel } from "@/components/State";

export default function NotFound() {
  const { t, locale } = useApp();
  return (
    <div className="section">
      <StatePanel kind="not_found" title={t("state.not_found_title")}
        actions={<><Link className="btn" href={`/${locale}/apps`}>{t("home.cta_apps")}</Link><Link className="btn ghost" href={`/${locale}/devices`}>{t("nav.devices")}</Link><Link className="btn ghost" href={`/${locale}`}>{t("state.home")}</Link></>}>
        {t("state.not_found_text")}
      </StatePanel>
    </div>
  );
}
