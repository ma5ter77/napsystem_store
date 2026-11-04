"""NAP-116: what a new app version changes compared with the version in the store ("было → стало").

The moderator (and the author) see one list of differences instead of comparing two screens by eye: store texts per
language, the cover, sessions added / removed / with new sound, versions for speakers and the chair added or removed,
the questionnaires and the required equipment.
"""
from __future__ import annotations

from ..models import AppVersion

TEXT_FIELDS = ("title", "subtitle", "description")


def _sessions(v: AppVersion) -> dict:
    return {s.key: s for s in v.sessions}


def version_changes(live: AppVersion | None, v: AppVersion) -> list[dict]:
    """Differences of `v` against `live`. Every item: {area, kind: added|removed|changed, key?, locale?, before, after}.
    With no version in the store everything is new, so the list is empty (the review shows the version itself)."""
    if live is None or live.id == v.id:
        return []
    out: list[dict] = []
    before_meta, after_meta = live.metadata_ or {}, v.metadata_ or {}
    for loc in sorted(set(before_meta) | set(after_meta)):
        b, a = before_meta.get(loc) or {}, after_meta.get(loc) or {}
        for f in TEXT_FIELDS:
            bv, av = (b.get(f) or "").strip(), (a.get(f) or "").strip()
            if bv != av:
                out.append({"area": "text", "key": f, "locale": loc,
                            "kind": "added" if not bv else "removed" if not av else "changed", "before": bv, "after": av})
    if live.cover_asset_id != v.cover_asset_id:
        out.append({"area": "cover", "kind": "added" if not live.cover_asset_id else "removed" if not v.cover_asset_id else "changed",
                    "before": None, "after": None})
    bs, as_ = _sessions(live), _sessions(v)
    for key in [k for k in as_ if k not in bs]:
        out.append({"area": "session", "key": key, "kind": "added", "before": None, "after": as_[key].titles})
    for key in [k for k in bs if k not in as_]:
        out.append({"area": "session", "key": key, "kind": "removed", "before": bs[key].titles, "after": None})
    for key in [k for k in as_ if k in bs]:
        b, a = bs[key], as_[key]
        if (b.titles or {}) != (a.titles or {}):
            out.append({"area": "session_title", "key": key, "kind": "changed", "before": b.titles, "after": a.titles})
        if b.sha256 != a.sha256:
            out.append({"area": "session_sound", "key": key, "kind": "changed",
                        "before": round(b.duration_seconds), "after": round(a.duration_seconds)})
        bl, al = set(b.variants or {}), set(a.variants or {})
        for lay in sorted(al - bl):
            out.append({"area": "layout", "key": key, "kind": "added", "before": None, "after": lay})
        for lay in sorted(bl - al):
            out.append({"area": "layout", "key": key, "kind": "removed", "before": lay, "after": None})
        for lay in sorted(al & bl):
            if (b.variants[lay] or {}).get("sha256") != (a.variants[lay] or {}).get("sha256"):
                out.append({"area": "layout", "key": key, "kind": "changed", "before": lay, "after": lay})
    if [s.key for s in live.sessions if s.key in as_] != [s.key for s in v.sessions if s.key in bs]:
        out.append({"area": "order", "kind": "changed", "before": [s.key for s in live.sessions], "after": [s.key for s in v.sessions]})
    for f in ("questionnaire_pre", "questionnaire_post"):
        if getattr(live, f) != getattr(v, f):
            out.append({"area": "questionnaire", "key": f, "kind": "changed", "before": getattr(live, f), "after": getattr(v, f)})
    bc = set((live.requirements or {}).get("required_capabilities") or [])
    ac = set((v.requirements or {}).get("required_capabilities") or [])
    if bc != ac:
        out.append({"area": "equipment", "kind": "changed", "before": sorted(bc), "after": sorted(ac)})
    return out


def live_summary(live: AppVersion | None) -> dict | None:
    if live is None:
        return None
    return {"id": str(live.id), "version_label": live.version_label, "number": live.number,
            "published_at": live.published_at.isoformat() if live.published_at else None,
            "metadata": live.metadata_, "sessions": len(live.sessions)}
