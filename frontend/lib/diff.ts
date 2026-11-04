// NAP-116: word-level "было → стало" for store texts. Longest common subsequence over words and spaces, so an edit in
// a long description shows only the words that changed.

export type Piece = { text: string; op: "same" | "del" | "ins" };

const MAX_CELLS = 1_500_000;   // ~1200 × 1200 tokens; longer texts fall back to "whole text replaced"

export function wordDiff(before: string, after: string): Piece[] {
  if (before === after) return before ? [{ text: before, op: "same" }] : [];
  const a = before.split(/(\s+)/).filter(Boolean);
  const b = after.split(/(\s+)/).filter(Boolean);
  if (!a.length) return [{ text: after, op: "ins" }];
  if (!b.length) return [{ text: before, op: "del" }];
  if (a.length * b.length > MAX_CELLS) return [{ text: before, op: "del" }, { text: after, op: "ins" }];
  const n = a.length, m = b.length;
  const L: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: Piece[] = [];
  const push = (text: string, op: Piece["op"]) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text; else out.push({ text, op });
  };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { push(a[i], "same"); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) push(a[i++], "del");
    else push(b[j++], "ins");
  }
  while (i < n) push(a[i++], "del");
  while (j < m) push(b[j++], "ins");
  return out;
}
