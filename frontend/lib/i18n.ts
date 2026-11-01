import ru from "@/messages/ru.json";
import en from "@/messages/en.json";

export const LOCALES = ["ru", "en"] as const;
export type Locale = (typeof LOCALES)[number];
const DICTS: Record<Locale, Record<string, unknown>> = { ru, en };

export function isLocale(x: string): x is Locale {
  return (LOCALES as readonly string[]).includes(x);
}

function lookup(d: Record<string, unknown>, key: string): string | undefined {
  let cur: unknown = d;
  for (const part of key.split(".")) {
    if (cur && typeof cur === "object" && part in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[part];
    } else return undefined;
  }
  return typeof cur === "string" ? cur : undefined;
}

const PLURAL = /\{(\w+), plural,((?:\s*=?\w+\s*\{[^{}]*\})+)\s*\}/g;
const FORM = /(=?\w+)\s*\{([^{}]*)\}/g;  // "=1 {...}": an exact number wins over the plural category

export function format(template: string, locale: Locale, params: Record<string, string | number> = {}): string {
  const pr = new Intl.PluralRules(locale);
  let out = template.replace(PLURAL, (_m, name: string, body: string) => {
    const n = Number(params[name] ?? 0);
    const forms: Record<string, string> = {};
    for (const f of body.matchAll(FORM)) forms[f[1]] = f[2];
    const text = forms[`=${n}`] ?? forms[pr.select(n)] ?? forms.other ?? "";
    return text.replace(/#/g, new Intl.NumberFormat(locale).format(n));
  });
  for (const [k, v] of Object.entries(params)) out = out.split(`{${k}}`).join(String(v));
  return out;
}

/** NAP-084: every built-in UI text as a flat key -> text map (for the content editor). */
export function flatDict(locale: Locale): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (o: Record<string, unknown>, prefix: string) => {
    for (const [k, v] of Object.entries(o)) {
      if (typeof v === "string") out[prefix + k] = v;
      else if (v && typeof v === "object") walk(v as Record<string, unknown>, `${prefix}${k}.`);
    }
  };
  walk(DICTS[locale], "");
  return out;
}

/** overrides: texts published by a content editor (NAP-084) — they win over the built-in dictionary. */
export function translator(locale: Locale, overrides: Record<string, string> = {}) {
  const d = DICTS[locale];
  return (key: string, params?: Record<string, string | number>) => {
    const tpl = overrides[key] ?? lookup(d, key) ?? lookup(DICTS.ru, key);
    if (tpl === undefined) {
      if (process.env.NODE_ENV !== "production") console.warn("missing i18n key", key);
      return key;
    }
    return format(tpl, locale, params);
  };
}

export type T = ReturnType<typeof translator>;

/** Locale changes number formatting only — never the currency or amount. */
/** One decimal in the locale format: 4,2 (ru) / 4.2 (en). */
export function num1(x: number, locale: Locale): string {
  return new Intl.NumberFormat(locale === "ru" ? "ru-RU" : "en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(x);
}

export function money(minor: number | null | undefined, currency: string, locale: Locale): string {
  if (minor === null || minor === undefined) return "—";
  return new Intl.NumberFormat(locale === "ru" ? "ru-RU" : "en-GB", { style: "currency", currency }).format(minor / 100);
}

export function date(iso: string | null | undefined, locale: Locale, withTime = false): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(locale === "ru" ? "ru-RU" : "en-GB", {
    dateStyle: "medium", ...(withTime ? { timeStyle: "short" } : {}),
  }).format(new Date(iso));
}

export function duration(seconds: number, locale: Locale): string {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return locale === "ru" ? `${m} мин ${s} с` : `${m} min ${s} s`;
}

export function langName(code: string | null | undefined, locale: Locale): string {
  if (!code || code === "none") return locale === "ru" ? "без речи" : "no speech";
  try {
    return new Intl.DisplayNames([locale], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export function swapLocalePath(path: string, to: Locale): string {
  const parts = path.split("/");
  if (isLocale(parts[1])) parts[1] = to;
  else parts.splice(1, 0, to);
  return parts.join("/") || `/${to}`;
}


/** NAP-095: "(демо)" / "(demo, ready)" are data markers, not part of the name — the UI shows a separate Demo badge. */
export function displayTitle(title: string | null | undefined): string {
  return (title ?? "").replace(/\s*\((?:демо|demo)(?:,\s*([^)]+))?\)/i, (_m, rest) => (rest ? ` (${rest})` : "")).trim();
}
