#!/usr/bin/env python3
"""Copies the release metadata and versioned documents into the backend package so the running product
serves exactly what the repository says. Run after editing release.json, docs/spec/*, docs/legal/*, docs/PROJECT.md or
CHANGELOG.md:  python3 ops/release.py   (a backend test fails if the copies are stale)."""
import argparse, json, pathlib, shutil, sys

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if packaged documents differ; do not write")
    args = parser.parse_args()
    rel = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))
    if not (ROOT / "docs" / rel["spec"]["document"]).exists():
        sys.exit(f"spec document missing: docs/{rel['spec']['document']}")
    if f"## [{rel['version']}]" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        sys.exit(f"CHANGELOG.md has no entry for {rel['version']}")
    expected = {name: src.read_bytes() for src, name in sources()}
    actual = {p.relative_to(DST).as_posix(): p.read_bytes() for p in DST.rglob("*") if p.is_file()}
    if args.check:
        if actual != expected:
            sys.exit("Packaged release documents are stale; run python3 ops/release.py")
        print("Packaged release documents match source bytes")
        return
    # Remove only stale package files; never recursively delete a calculated directory.
    for name in actual.keys() - expected.keys():
        (DST / name).unlink()
    for src, rel_path in sources():
        out = DST / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
    print(f"release {rel['version']} (spec {rel['spec']['version']}) -> {DST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
