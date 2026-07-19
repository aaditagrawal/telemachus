#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

VERSION="${1:-}"
if [[ ! "$VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "Usage: $0 <v?major.minor.patch[-prerelease]>" >&2
    exit 1
fi
grep -Fq "## [$VERSION]" CHANGELOG.md || {
    echo "CHANGELOG.md has no section for $VERSION." >&2
    exit 1
}
UNRELEASED="$(awk '
    /^## \[Unreleased\]$/ {flag=1; next}
    flag && /^---$/ {exit}
    flag {print}
' CHANGELOG.md)"
if printf '%s\n' "$UNRELEASED" |
   grep -Eq '^### (Added|Changed|Deprecated|Removed|Fixed|Security)$'; then
    echo "Move completed Unreleased entries into the $VERSION section before tagging." >&2
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "Release aborted: the worktree is not clean." >&2
    echo "Commit the exact reviewed release contents yourself; this script will not stage them." >&2
    git status --short
    exit 1
fi

BRANCH="$(git symbolic-ref --quiet --short HEAD)" || {
    echo "Release aborted: detached HEAD." >&2
    exit 1
}
if [ "$BRANCH" != "main" ]; then
    echo "Release aborted: releases must be tagged from main, not '$BRANCH'." >&2
    exit 1
fi
ORIGIN_URL="$(git remote get-url origin 2>/dev/null)" || {
    echo "Release aborted: configure a Telemachus-owned 'origin' remote first." >&2
    exit 1
}
if UPSTREAM_URL="$(git remote get-url upstream 2>/dev/null)" &&
   [ "$ORIGIN_URL" = "$UPSTREAM_URL" ]; then
    echo "Release aborted: origin and upstream point to the same repository." >&2
    exit 1
fi
if git show-ref --verify --quiet "refs/tags/$VERSION" ||
   git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1; then
    echo "Release aborted: tag $VERSION already exists locally or on origin." >&2
    exit 1
fi

echo "Running release checks..."
(
    cd MacHost
    swift test
    swift run Telemachus --transport-self-test
    swift build -c release
)
(
    cd AndroidClient
    ./gradlew --no-daemon testDebugUnitTest lintDebug assembleDebug
)

if [ "${TELEMACHUS_RELEASE_CONFIRM:-}" != "$VERSION" ]; then
    echo "Checks passed. No changes were pushed." >&2
    echo "Re-run with TELEMACHUS_RELEASE_CONFIRM=$VERSION to create and push the tag." >&2
    exit 2
fi

git fetch origin main --tags
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    echo "Release aborted: HEAD must exactly match origin/main." >&2
    exit 1
fi

git tag -a "$VERSION" -m "Telemachus $VERSION"
git push --atomic origin \
    "HEAD:refs/heads/main" \
    "refs/tags/$VERSION:refs/tags/$VERSION"
echo "Pushed Telemachus $VERSION to $ORIGIN_URL."
