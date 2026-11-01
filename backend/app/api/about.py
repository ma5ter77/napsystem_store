"""Product version, the spec version it implements, and the versioned documents behind it."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ..config import get_settings
from ..errors import ApiError

router = APIRouter(tags=["about"])
DATA = Path(__file__).resolve().parent.parent / "release_data"


@lru_cache
def release() -> dict:
    return json.loads((DATA / "release.json").read_text(encoding="utf-8"))


def documents() -> dict[str, Path]:
    docs = {"changelog": DATA / "CHANGELOG.md", "project": DATA / "PROJECT.md"}
    for p in sorted((DATA / "spec").glob("TZ-v*.md")):
        docs["spec-" + p.stem.removeprefix("TZ-v")] = p
    return docs


@lru_cache
def legal_index() -> dict:
    """NAP-062: legal documents with their current version (docs/legal/legal.json)."""
    p = DATA / "legal" / "legal.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"documents": {}}


def legal_version(key: str) -> str | None:
    d = legal_index()["documents"].get(key)
    return d["version"] if d else None


@router.get("/legal")
def legal():
    out = []
    for key, d in legal_index()["documents"].items():
        out.append({"key": key, "version": d["version"], "date": d["date"], "title": d["title"],
                    "languages": [l for l in ("ru", "en") if (DATA / "legal" / f"{key}.{l}.md").exists()]})
    return {"status": legal_index().get("status"), "documents": out}


@router.get("/legal/{key}/{lang}", response_class=PlainTextResponse)
def legal_document(key: str, lang: str):
    if key not in legal_index()["documents"] or lang not in ("ru", "en"):
        raise ApiError("not_found", 404, entity="document")
    p = DATA / "legal" / f"{key}.{lang}.md"
    if not p.exists():
        raise ApiError("not_found", 404, entity="document")
    return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@router.get("/version")
def version():
    r = release()
    return {**r, "environment": get_settings().env, "build": os.environ.get("NAP_BUILD", "local"),
            "documents": sorted(documents())}


@router.get("/about/documents/{name}", response_class=PlainTextResponse)
def document(name: str):
    p = documents().get(name)
    if p is None:
        raise ApiError("not_found", 404, entity="document")
    return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
