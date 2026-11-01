#!/usr/bin/env bash
# Inactive candidate. Plan-only by default; see docs/release-safety.md.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/release_control.py" deploy "$@"
