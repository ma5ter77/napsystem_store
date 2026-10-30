"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { Locale, swapLocalePath } from "@/lib/i18n";
import { BrandMark } from "./Art";
import { useApp } from "./Providers";

export function Header() {
  const { locale, t, me, cartCount, refreshMe } = useApp();
  const path = usePathname() || `/${locale}`;
  const search = useSearchParams();
  const router = useRouter();
  const q = search?.toString() ? `?${search}` : "";
  const nav = [
    { href: `/${locale}/apps`, label: t("nav.apps") },
    { href: `/${locale}/devices`, label: t("nav.devices") },
    { href: `/${locale}/kits`, label: t("nav.kits") },
  ];
  const roles = me?.roles ?? [];

  async function switchTo(to: Locale) {
    // keeps the current card/route, session and carts; never creates products or orders
    if (me) { try { await api("/me", { method: "PATCH", body: { locale: to } }); } catch { /* cookie still set */ } }
    document.cookie = `nap_locale=${to}; path=/; max-age=31536000; samesite=lax`;
    router.push(swapLocalePath(path, to) + q);
  }

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    await refreshMe();
    router.push(`/${locale}`);
  }

  return (
    <header className="top">
      <div className="wrap">
        <Link href={`/${locale}`} className="brand"><BrandMark /> NAP Store</Link>
        <nav className="nav" aria-label={t("nav.main")}>
          {nav.map((n) => (
            <Link key={n.href} href={n.href} aria-current={path.startsWith(n.href) ? "page" : undefined}>{n.label}</Link>
          ))}
        </nav>
        <div className="tools">
          <Link className="btn ghost small" href={`/${locale}/cart`}>{t("nav.cart", { count: cartCount })}</Link>
          {me ? (
            <>
              <Link className="btn ghost small" href={`/${locale}/library`}>{t("nav.library")}</Link>
              <details className="menu">
                <summary className="btn ghost small">{me.display_name}</summary>
                <div className="panel flat stack" style={{ position: "absolute", right: 20, marginTop: 8, minWidth: 220, zIndex: 30 }}>
                  <Link href={`/${locale}/account`}>{t("nav.account")}</Link>
                  <Link href={`/${locale}/orders`}>{t("nav.orders")}</Link>
                  {roles.includes("developer") && <Link href={`/${locale}/developer`}>{t("nav.developer")}</Link>}
                  {(roles.includes("seller") || roles.includes("fulfillment")) && <Link href={`/${locale}/seller`}>{t("nav.seller")}</Link>}
                  {(roles.includes("moderator") || roles.includes("admin")) && <Link href={`/${locale}/moderation`}>{t("nav.moderation")}</Link>}
                  {roles.includes("admin") && <Link href={`/${locale}/admin`}>{t("nav.admin")}</Link>}
                  <button className="btn ghost small" onClick={logout}>{t("nav.logout")}</button>
                </div>
              </details>
            </>
          ) : me === null ? (
            <Link className="btn small" href={`/${locale}/login?next=${encodeURIComponent(path)}`}>{t("nav.login")}</Link>
          ) : null}
          <span className="lang" role="group" aria-label={t("nav.language")}>
            {(["ru", "en"] as Locale[]).map((l) =>
              l === locale ? <span key={l} aria-current="true">{l.toUpperCase()}</span> :
                <a key={l} href={swapLocalePath(path, l) + q} hrefLang={l} lang={l}
                   onClick={(e) => { e.preventDefault(); switchTo(l); }}>{l.toUpperCase()}</a>)}
          </span>
        </div>
      </div>
    </header>
  );
}
