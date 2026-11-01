"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, useAction } from "@/components/ui";
import { api } from "@/lib/api";
import { LegalText } from "@/components/LegalText";

function Register() {
  const { t, locale, refreshMe, refreshCart } = useApp();
  const router = useRouter();
  const next = useSearchParams()?.get("next") || `/${locale}`;
  const [f, setF] = useState({ email: "", password: "", display_name: "", as_developer: false, accept_terms: false, consent_personal_data: false });
  const labels = { terms: t("legal.terms"), privacy: t("legal.privacy_with"), consent: t("legal.consent") };
  const act = useAction();
  return (
    <div className="section" style={{ maxWidth: 520 }}>
      <h1>{t("auth.register_title")}</h1>
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const r = await act.run(() => api("/auth/register", { body: { ...f, locale } }));
        if (r) { await refreshMe(); await refreshCart(); router.push(f.as_developer ? `/${locale}/developer` : next); }
      }}>
        <label className="field">{t("auth.name")}<input required value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></label>
        <label className="field">{t("auth.email")}<input type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
        <label className="field">{t("auth.password")}<input type="password" required minLength={10} autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
          <span className="hint">{t("auth.password_hint")}</span></label>
        <label className="check"><input type="checkbox" checked={f.as_developer} onChange={(e) => setF({ ...f, as_developer: e.target.checked })} /> {t("auth.as_developer")}</label>
        <fieldset className="stack legal-checks">
          <label className="check"><input type="checkbox" required checked={f.accept_terms} onChange={(e) => setF({ ...f, accept_terms: e.target.checked })} />{" "}
            <span><LegalText text={t("legal.reg_terms")} locale={locale} labels={labels} /></span></label>
          <label className="check"><input type="checkbox" required checked={f.consent_personal_data} onChange={(e) => setF({ ...f, consent_personal_data: e.target.checked })} />{" "}
            <span><LegalText text={t("legal.reg_consent")} locale={locale} labels={labels} /></span></label>
          <span className="hint">{t("legal.reg_hint")}</span>
        </fieldset>
        <ErrorBox error={act.error} />
        <button className="btn" disabled={act.pending}>{t("auth.register")}</button>
      </form>
    </div>
  );
}

export default function RegisterPage() {
  return <Suspense><Register /></Suspense>;
}
