"use client";
// NAP-012: buyers choose the currency they see and pay in (when the author priced the app in it).
import { useRouter } from "next/navigation";
import { useApp } from "./Providers";

export function CurrencySwitch({ current, options }: { current: string; options: string[] }) {
  const { t } = useApp();
  const router = useRouter();
  return (
    <span className="lang" role="group" aria-label={t("footer.currency")}>
      {options.map((c) => c === current ? <span key={c} aria-current="true">{c}</span> :
        <a key={c} href="#" onClick={(e) => {
          e.preventDefault();
          document.cookie = `nap_currency=${c}; path=/; max-age=31536000; samesite=lax`;
          router.refresh();
        }}>{c}</a>)}
    </span>
  );
}
