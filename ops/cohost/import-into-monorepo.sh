#!/usr/bin/env bash
# NAP-085: move the store into the shared repository BrothersB/nap-trainer as apps/store, KEEPING its git history.
#   bash import-into-monorepo.sh <path-to-nap-store-repo> <path-to-nap-trainer-clone> [base-branch]
# Creates branch codex/store-development from the agreed base (default: origin's default branch), merges the store
# history under apps/store with one merge commit. Nothing is pushed; review, then `git push -u origin codex/store-development`
# and open a Pull Request. Never force-push; never touch other branches.
set -euo pipefail
STORE="$(cd "${1:?store repo}" && pwd)"; MONO="$(cd "${2:?nap-trainer clone}" && pwd)"
cd "$MONO"
[ -z "$(git status --porcelain)" ] || { echo "nap-trainer clone has uncommitted changes — stop"; exit 1; }
git fetch origin
BASE="${3:-$(git remote show origin | sed -n 's/.*HEAD branch: //p')}"
git show-ref --verify --quiet refs/heads/codex/store-development && { echo "branch codex/store-development already exists locally — inspect it first"; exit 1; }
git ls-remote --exit-code --heads origin codex/store-development >/dev/null && { echo "codex/store-development exists on origin — inspect its owner/history first"; exit 1; }
[ -e apps/store ] && { echo "apps/store already exists in $BASE — agree on the location first"; exit 1; }
git switch -c codex/store-development "origin/$BASE"
SB=$(git -C "$STORE" rev-parse --abbrev-ref HEAD)
git remote add store-import "$STORE"
git fetch store-import "$SB"
git merge -s ours --no-commit --allow-unrelated-histories "store-import/$SB"
git read-tree --prefix=apps/store/ -u "store-import/$SB"
git commit -m "Import NAP Store into apps/store with its history (store $(git -C "$STORE" rev-parse --short HEAD), NAP-085)"
git remote remove store-import
# the store CI lives in apps/store/.github; GitHub only runs workflows from the repository root
mkdir -p .github/workflows
sed -e 's#working-directory: backend#working-directory: apps/store/backend#' \
    -e 's#working-directory: frontend#working-directory: apps/store/frontend#' \
    -e 's#cache-dependency-path: frontend/#cache-dependency-path: apps/store/frontend/#' \
    -e 's#^name: ci#name: store-ci#' apps/store/.github/workflows/ci.yml > .github/workflows/store-ci.yml
echo "Review .github/workflows/store-ci.yml (paths filter, docker job working directory), then commit it separately."
echo "Done: branch codex/store-development, nothing pushed."
