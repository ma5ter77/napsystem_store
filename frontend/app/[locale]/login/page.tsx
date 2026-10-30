"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useApp } from "@/components/Providers";
import { ErrorBox, useAction } from "@/components/ui";
import { api } from "@/lib/api";

function Login() {
  const { t, locale, refreshMe, refreshCart } = useApp();
  const router = useRouter();
  const next = useSearchParams()?.get("next") || `/${locale}`;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const act = useAction();
  return (
    <div className="section" style={{ maxWidth: 480 }}>
      <h1>{t("auth.login_title")}</h1>
      <form className="form" onSubmit={async (e) => {
        e.preventDefault();
        const r = await act.run(() => api("/auth/login", { body: { email, password } }));
        if (r) {
          await refreshMe(); await refreshCart();
          // the profile language wins after login; stay on the same page
          const target = next.startsWith("/") ? next.replace(/^\/(ru|en)(?=\/|$)/, `/${r.locale}`) : `/${r.locale}`;
          router.push(target);
        }
      }}>
        <label className="field">{t("auth.email")}<input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="field">{t("auth.password")}<input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <ErrorBox error={act.error} />
        <button className="btn" disabled={act.pending}>{t("auth.login")}</button>
        <p className="hint">{t("auth.no_account")} <Link href={`/${locale}/register?next=${encodeURIComponent(next)}`}>{t("auth.register")}</Link></p>
      </form>
      <details className="panel flat" style={{ marginTop: 24 }}>
        <summary>{t("auth.demo_accounts")}</summary>
        <p className="hint">{t("auth.demo_password")}: <code>demo-password-1</code></p>
        <ul className="hint">
          {["user@nap.demo", "buyer.en@nap.demo", "dev1@nap.demo", "dev2@nap.demo", "moderator@nap.demo", "admin@nap.demo",
            "seller@nap.demo", "fulfillment@nap.demo", "seller2@nap.demo"].map((e) => (
            <li key={e}><button type="button" className="btn ghost small" onClick={() => { setEmail(e); setPassword("demo-password-1"); }}>{e}</button></li>
          ))}
        </ul>
      </details>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense><Login /></Suspense>;
}
