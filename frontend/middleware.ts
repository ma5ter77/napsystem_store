import { NextRequest, NextResponse } from "next/server";

const LOCALES = ["ru", "en"];

function pick(req: NextRequest): string {
  const c = req.cookies.get("nap_locale")?.value;
  if (c && LOCALES.includes(c)) return c;
  const al = req.headers.get("accept-language") || "";
  for (const part of al.split(",")) {
    const tag = part.split(";")[0].trim().slice(0, 2).toLowerCase();
    if (LOCALES.includes(tag)) return tag;
  }
  return "ru"; // fallback language
}

export function middleware(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  const first = pathname.split("/")[1];
  if (LOCALES.includes(first)) {
    const res = NextResponse.next();
    if (req.cookies.get("nap_locale")?.value !== first) {
      res.cookies.set("nap_locale", first, { path: "/", maxAge: 31536000, sameSite: "lax" });
    }
    return res;
  }
  const url = req.nextUrl.clone();
  url.pathname = `/${pick(req)}${pathname === "/" ? "" : pathname}`;
  url.search = search;
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/((?!api/|_next/|favicon|robots.txt|.*\\..*).*)"] };
