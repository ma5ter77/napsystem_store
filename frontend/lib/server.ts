import "server-only";
import { cookies } from "next/headers";
import { Locale, translator } from "@/lib/i18n";

const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8000";

/** Server-side fetch for SSR of public pages. Forwards the user's cookies so personalised fields render. */
export async function serverApi<T = any>(path: string, locale: string): Promise<{ ok: true; data: T } | { ok: false; status: number; error: any }> {
  const jar = await cookies();
  const cookieHeader = jar.getAll().map((c) => `${c.name}=${c.value}`).join("; ");
  try {
    const res = await fetch(`${BACKEND}/api/v1${path}`, {
      headers: { "x-locale": locale, cookie: cookieHeader }, cache: "no-store",
    });
    const json = await res.json();
    if (!res.ok) return { ok: false, status: res.status, error: json.error };
    return { ok: true, data: json as T };
  } catch (e) {
    return { ok: false, status: 503, error: { code: "network_error" } };
  }
}

export const SITE = process.env.PUBLIC_BASE_URL || "http://localhost:3000";

export function alternates(locale: string, path: string) {
  return {
    canonical: `${SITE}/${locale}${path}`,
    languages: { ru: `${SITE}/ru${path}`, en: `${SITE}/en${path}`, "x-default": `${SITE}/ru${path}` },
  };
}

/** NAP-084: server-rendered pages use the texts published by the content editor too. */
export async function serverTranslator(locale: Locale) {
  try {
    const res = await fetch(`${BACKEND}/api/v1/content/texts?locale=${locale}`, { next: { revalidate: 10 } });
    if (res.ok) return translator(locale, ((await res.json()).texts ?? {}) as Record<string, string>);
  } catch { /* backend unavailable: built-in texts */ }
  return translator(locale);
}
