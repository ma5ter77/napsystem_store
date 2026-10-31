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
