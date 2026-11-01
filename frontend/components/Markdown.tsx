// NAP-062: a small Markdown renderer for the legal documents (headings, paragraphs, lists, tables, quotes,
// bold/italic, links). It builds React elements only — no raw HTML from the document is ever injected.
import Link from "next/link";
import { Fragment, type ReactNode } from "react";

function inline(text: string, key = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${key}-${n++}`;
    if (tok.startsWith("**")) out.push(<strong key={k}>{inline(tok.slice(2, -2), k)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("[")) {
      const [, label, href] = tok.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/) ?? [];
      const safe = /^(https?:\/\/|\/|mailto:|#)/.test(href ?? "");
      out.push(safe ? (href.startsWith("/") ? <Link key={k} href={href}>{label}</Link> : <a key={k} href={href} rel="noopener noreferrer">{label}</a>) : label);
    } else out.push(<em key={k}>{inline(tok.slice(1, -1), k)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function slug(s: string) {
  return s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 60);
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let b = 0;
  const isTable = (l: string) => /^\s*\|.*\|\s*$/.test(l);
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    const h = l.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const level = h[1].length;
      const Tag = (`h${Math.min(level + 1, 5)}`) as "h2";  // the page title is the h1
      blocks.push(level === 1 ? <h1 key={b++}>{inline(h[2])}</h1> : <Tag key={b++} id={slug(h[2])}>{inline(h[2])}</Tag>);
      i++; continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(l.trim())) { blocks.push(<hr key={b++} />); i++; continue; }
    if (l.startsWith(">")) {
      const q: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) { q.push(lines[i].replace(/^>\s?/, "")); i++; }
      blocks.push(<blockquote key={b++} className="notice">{q.join(" ").split(/\s{2,}/).map((x, k) => <p key={k}>{inline(x)}</p>)}</blockquote>);
      continue;
    }
    if (isTable(l) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const cells = (r: string) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(l);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isTable(lines[i])) { rows.push(cells(lines[i])); i++; }
      blocks.push(<div key={b++} className="scroll-x"><table><thead><tr>{head.map((c, k) => <th key={k}>{inline(c)}</th>)}</tr></thead>
        <tbody>{rows.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (/^\s*([-*]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]\s+/.test(l);
      const start = ordered ? Number(l.match(/^\s*(\d+)/)?.[1] ?? 1) : undefined;
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+[.)])\s+/.test(lines[i])) {
        let item = lines[i].replace(/^\s*([-*]|\d+[.)])\s+/, "");
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])) { item += " " + lines[i].trim(); i++; }
        items.push(item);
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={b++} start={start}>{items.map((x, k) => <li key={k}>{inline(x)}</li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|>|\s*\|)/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])) { para.push(lines[i].trim()); i++; }
    blocks.push(<p key={b++}>{inline(para.join(" "))}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
