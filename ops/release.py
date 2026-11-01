#!/usr/bin/env python3
"""Copies the release metadata and versioned documents into the backend package so the running product
serves exactly what the repository says. Run after editing release.json, docs/spec/*, docs/legal/*, docs/PROJECT.md or
CHANGELOG.md:  python3 ops/release.py   (a backend test fails if the copies are stale)."""
import json, pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DST = ROOT / "backend" / "app" / "release_data"


def sources():
    yield ROOT / "release.json", "release.json"
    yield ROOT / "CHANGELOG.md", "CHANGELOG.md"
    yield ROOT / "docs" / "PROJECT.md", "PROJECT.md"
    for p in sorted((ROOT / "docs" / "spec").glob("TZ-v*.md")):
        yield p, f"spec/{p.name}"
    for p in sorted((ROOT / "docs" / "legal").glob("*")):  # NAP-062: offer, privacy policy, consents (RU/EN)
        if p.suffix in (".md", ".json"):
            yield p, f"legal/{p.name}"


def main():
    rel = json.loads((ROOT / "release.json").read_text())
    if not (ROOT / "docs" / rel["spec"]["document"]).exists():
        sys.exit(f"spec document missing: docs/{rel['spec']['document']}")
    if f"## [{rel['version']}]" not in (ROOT / "CHANGELOG.md").read_text():
        sys.exit(f"CHANGELOG.md has no entry for {rel['version']}")
    if DST.exists():
        shutil.rmtree(DST)
    for src, rel_path in sources():
        out = DST / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
    print(f"release {rel['version']} (spec {rel['spec']['version']}) -> {DST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
