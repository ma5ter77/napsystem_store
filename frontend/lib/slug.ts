/**
 * App address (slug) rules, mirrored from backend SLUG_RE = ^[a-z0-9][a-z0-9-]{2,79}$.
 * The HTML `pattern` attribute is compiled with the `v` flag in current browsers, where an unescaped
 * trailing "-" in a character class is a syntax error and the whole pattern is silently ignored,
 * so the hyphen must be escaped (scripts/check-patterns.mjs guards every pattern in the UI).
 */
export const SLUG_PATTERN = "[a-z0-9][a-z0-9\\-]{2,79}";

/** Typing helper: lowercase Latin, "_" and spaces become "-". Other characters are left for the pattern to reject. */
export function normalizeSlug(value: string): string {
  return value.toLowerCase().replace(/[_\s]+/g, "-").replace(/-{2,}/g, "-");
}
