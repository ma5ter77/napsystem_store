// NAP-062: a translated sentence with links to legal documents, e.g. "I accept the {terms} and have read the {privacy}".
import Link from "next/link";
import { Fragment } from "react";

const DOC: Record<string, string> = { terms: "terms", privacy: "privacy", consent: "consent-personal-data",
  measurements: "consent-measurements" };

export function LegalText({ text, locale, labels }: { text: string; locale: string; labels: Record<string, string> }) {
  const parts = text.split(/(\{\w+\})/);
  return (
    <>{parts.map((p, i) => {
      const m = p.match(/^\{(\w+)\}$/);
      if (m && DOC[m[1]]) return <Link key={i} href={`/${locale}/legal/${DOC[m[1]]}`} target="_blank" rel="noopener">{labels[m[1]] ?? m[1]}</Link>;
      return <Fragment key={i}>{p}</Fragment>;
    })}</>
  );
}
