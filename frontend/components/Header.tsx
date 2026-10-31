"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
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
  const accountLinks = me ? [
    { href: `/${locale}/account`, label: t("nav.account") },
    { href: `/${locale}/orders`, label: t("nav.orders") },
    ...(roles.includes("developer") ? [{ href: `/${locale}/developer`, label: t("nav.developer") }] : []),
    ...(roles.includes("seller") || roles.includes("fulfillment") ? [{ href: `/${locale}/seller`, label: t("nav.seller") }] : []),
    ...(roles.includes("moderator") || roles.includes("admin") ? [{ href: `/${locale}/moderation`, label: t("nav.moderation") }] : []),
    ...(roles.includes("admin") ? [{ href: `/${locale}/admin`, label: t("nav.admin") }] : []),
  ] : [];
  // phone menu (NAP-041): one-row header, everything else behind a keyboard-accessible button
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => { setOpen(false); }, [path]);
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("a,button")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); toggleRef.current?.focus(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

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

  const langSwitch = (
    <span className="lang" role="group" aria-label={t("nav.language")}>
            {(["ru", "en"] as Locale[]).map((l) =>
              l === locale ? <span key={l} aria-current="true">{l.toUpperCase()}</span> :
                <a key={l} href={swapLocalePath(path, l) + q} hrefLang={l} lang={l}
                   onClick={(e) => { e.preventDefault(); switchTo(l); }}>{l.toUpperCase()}</a>)}
          </span>
  );

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
          <Link className="btn ghost small cart-link" href={`/${locale}/cart`}>{t("nav.cart", { count: cartCount })}</Link>
          <button ref={toggleRef} type="button" className="btn ghost small menu-toggle" aria-expanded={open}
                  aria-controls="mobile-menu" onClick={() => setOpen(!open)}>{open ? t("nav.close_menu") : t("nav.menu")}</button>
          {me ? (
            <>
              <Link className="btn ghost small" href={`/${locale}/library`}>{t("nav.library")}</Link>
              <details className="menu">
                <summary className="btn ghost small">{me.display_name}</summary>
                <div className="panel flat stack" style={{ position: "absolute", right: 20, marginTop: 8, minWidth: 220, zIndex: 30 }}>
                  {accountLinks.map((x) => <Link key={x.href} href={x.href}>{x.label}</Link>)}
                  <button className="btn ghost small" onClick={logout}>{t("nav.logout")}</button>
                </div>
              </details>
            </>
          ) : me === null ? (
            <Link className="btn small" href={`/${locale}/login?next=${encodeURIComponent(path)}`}>{t("nav.login")}</Link>
          ) : null}
          {langSwitch}
        </div>
      </div>
      {open && (
        <div id="mobile-menu" ref={panelRef} className="mobile-menu" onClick={(e) => { if ((e.target as HTMLElement).closest("a")) setOpen(false); }}>
          <nav aria-label={t("nav.main")}>
            {nav.map((n) => <Link key={n.href} href={n.href} aria-current={path.startsWith(n.href) ? "page" : undefined}>{n.label}</Link>)}
          </nav>
          <div className="mobile-menu-group">
            {me ? (
              <>
                <strong>{me.display_name}</strong>
                <Link href={`/${locale}/library`}>{t("nav.library")}</Link>
                {accountLinks.map((x) => <Link key={x.href} href={x.href}>{x.label}</Link>)}
                <button className="btn ghost small" onClick={logout}>{t("nav.logout")}</button>
              </>
            ) : me === null ? <Link className="btn small" href={`/${locale}/login?next=${encodeURIComponent(path)}`}>{t("nav.login")}</Link> : null}
          </div>
          {langSwitch}
        </div>
      )}
    </header>
  );
}
