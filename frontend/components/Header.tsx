"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Locale, swapLocalePath } from "@/lib/i18n";
import { BrandMark } from "./Art";
import { BagIcon, MenuIcon, PersonIcon } from "./icons";
import { useApp } from "./Providers";

export function Header() {
  const app = useApp();
  const { locale, t, refreshMe } = app;
  // The header sits in a Suspense boundary and hydrates a moment after the page. If /me or the cart answered in
  // that moment, the first client render would differ from the server HTML (hydration error). So the header
  // shows the signed-in state and the cart count only once it has hydrated itself.
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const me = hydrated ? app.me : undefined;
  const cartCount = hydrated ? app.cartCount : 0;
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
    ...(roles.includes("content_editor") || roles.includes("admin") ? [{ href: `/${locale}/content`, label: t("nav.content") }] : []),
    ...(roles.includes("admin") ? [{ href: `/${locale}/admin`, label: t("nav.admin") }] : []),
  ] : [];
  // phone menu (NAP-041): one-row header, everything else behind a keyboard-accessible button
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => { setOpen(false); if (accountRef.current) accountRef.current.open = false; }, [path]);
  useEffect(() => {  // account menu: Escape or a click outside closes it
    const close = (e: Event) => {
      const d = accountRef.current;
      if (!d?.open) return;
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !d.contains(e.target as Node)) {
        d.open = false;
        if (e instanceof KeyboardEvent) d.querySelector("summary")?.focus();
      }
    };
    document.addEventListener("keydown", close);
    document.addEventListener("click", close);
    return () => { document.removeEventListener("keydown", close); document.removeEventListener("click", close); };
  }, []);
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
          <Link className="icon-btn cart-link" href={`/${locale}/cart`} aria-label={t("nav.cart_label", { count: cartCount })}
                aria-current={path.startsWith(`/${locale}/cart`) ? "page" : undefined}>
            <BagIcon />{cartCount > 0 && <span className="count" aria-hidden="true">{cartCount}</span>}
          </Link>
          {me ? (
            <details className="menu" ref={accountRef}>
              <summary className="icon-btn" aria-label={t("nav.account_menu")}><PersonIcon /></summary>
              <div className="menu-panel">
                <div className="who"><span className="hint">{t("nav.signed_in_as")}</span><strong>{me.display_name}</strong></div>
                <Link href={`/${locale}/library`} aria-current={path.startsWith(`/${locale}/library`) ? "page" : undefined}>{t("nav.library")}</Link>
                {accountLinks.map((x) => <Link key={x.href} href={x.href} aria-current={path.startsWith(x.href) ? "page" : undefined}>{x.label}</Link>)}
                <hr />
                <button className="item" onClick={logout}>{t("nav.logout")}</button>
              </div>
            </details>
          ) : me === null ? (
            <Link className="btn small ghost" href={`/${locale}/login?next=${encodeURIComponent(path)}`}>{t("nav.login")}</Link>
          ) : null}
          {langSwitch}
          <button ref={toggleRef} type="button" className="icon-btn menu-toggle" aria-expanded={open}
                  aria-controls="mobile-menu" aria-label={open ? t("nav.close_menu") : t("nav.menu")} onClick={() => setOpen(!open)}><MenuIcon open={open} /></button>
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
                <button className="item" onClick={logout}>{t("nav.logout")}</button>
              </>
            ) : me === null ? <Link className="btn small" href={`/${locale}/login?next=${encodeURIComponent(path)}`}>{t("nav.login")}</Link> : null}
          </div>
          {langSwitch}
        </div>
      )}
    </header>
  );
}
