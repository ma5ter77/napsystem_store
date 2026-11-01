"""NAP-028: full-text search with Russian and English morphology (PostgreSQL FTS).

Each query word must match (AND); a word matches if its Russian stem, its English stem or a word starting with it
("simple" dictionary, prefix) is in the document. So «медитации» finds «медитация», «course» finds «Course»,
«мед» finds «медитация» and a mixed query «сон sleep» works too. Results come with a rank (title words weigh more).

The documents are built on the fly from published translations plus slug/author (apps) or brand/model (products);
the catalog is small (hundreds of items). When it grows, a stored tsvector column with a GIN index can replace the
LATERAL subquery without changing this interface.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

MAX_WORDS = 8


def words(q: str | None) -> list[str]:
    return re.findall(r"\w+", (q or "").lower())[:MAX_WORDS]


def _tsquery(ws: list[str]) -> tuple[str, dict]:
    parts, params = [], {}
    for i, w in enumerate(ws):
        params[f"w{i}"], params[f"p{i}"] = w, f"{w}:*"
        parts.append(f"(plainto_tsquery('russian', :w{i}) || plainto_tsquery('english', :w{i})"
                     f" || to_tsquery('simple', :p{i}))")
    return " && ".join(parts), params


_DOC = """setweight(to_tsvector('russian', tx.title), 'A') || setweight(to_tsvector('english', tx.title), 'A')
       || setweight(to_tsvector('simple', tx.title), 'A')
       || setweight(to_tsvector('russian', tx.body), 'C') || setweight(to_tsvector('english', tx.body), 'C')
       || setweight(to_tsvector('simple', tx.body || ' ' || {extra}), 'B')"""

_TX = """SELECT coalesce(string_agg(t.data->>'title', ' '), '') AS title,
                coalesce(string_agg(v.value, ' '), '') AS body
         FROM translations t CROSS JOIN LATERAL jsonb_each_text(t.data) v
         WHERE t.entity_type = :etype AND t.entity_id = e.id {status}"""


def _rank(db: Session, table: str, etype: str, extra: str, ids: list[uuid.UUID], q: str, published_only: bool):
    ws = words(q)
    if not ws:
        return None
    tsq, params = _tsquery(ws)
    sql = f"""
        SELECT e.id, ts_rank(d.doc, qq.q) AS rank
        FROM {table} e
        CROSS JOIN LATERAL ({_TX.format(status="AND t.status = 'published'" if published_only else "")}) tx
        CROSS JOIN LATERAL (SELECT {_DOC.format(extra=extra)} AS doc) d
        CROSS JOIN (SELECT {tsq} AS q) qq
        WHERE e.id = ANY(:ids) AND d.doc @@ qq.q"""
    rows = db.execute(text(sql), params | {"ids": list(ids), "etype": etype}).all()
    return {r.id: float(r.rank) for r in rows}


def rank_apps(db: Session, ids: list[uuid.UUID], q: str | None) -> dict[uuid.UUID, float] | None:
    """{app_id: rank} for apps matching q; None when q has no words (no filtering)."""
    return _rank(db, "apps", "app", "e.slug || ' ' || replace(e.slug, '-', ' ') || ' ' || coalesce(e.author_name, '')",
                 ids, q, published_only=True)


def rank_products(db: Session, ids: list[uuid.UUID], q: str | None) -> dict[uuid.UUID, float] | None:
    return _rank(db, "products", "product",
                 "e.slug || ' ' || replace(e.slug, '-', ' ') || ' ' || coalesce(e.brand, '') || ' ' || coalesce(e.model, '')",
                 ids, q, published_only=False)
