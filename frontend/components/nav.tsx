// NAP-091: wayfinding — breadcrumbs and a page head with a one-line "what you can do here".
import Link from "next/link";

export type Crumb = { href?: string; label: string };

export function Breadcrumbs({ items, label }: { items: Crumb[]; label: string }) {
  return (
    <nav className="crumbs" aria-label={label}>
      <ol>
        {items.map((c, i) => (
          <li key={i}>{c.href && i < items.length - 1 ? <Link href={c.href}>{c.label}</Link>
            : <span aria-current={i === items.length - 1 ? "page" : undefined}>{c.label}</span>}</li>
        ))}
      </ol>
    </nav>
  );
}

export function PageHead({ title, lead, children }: { title: React.ReactNode; lead?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="page-head">
      <h1>{title}</h1>
      {lead && <p>{lead}</p>}
      {children}
    </header>
  );
}
