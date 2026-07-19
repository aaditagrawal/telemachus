#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

CURRENT_VERSION="$(
    git -C "$ROOT_DIR" tag --list '[0-9]*.[0-9]*.[0-9]*' \
        --sort=-v:refname | head -n 1
)"
CURRENT_VERSION="${CURRENT_VERSION:-0.0.0}"
echo "Current version: $CURRENT_VERSION"

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"

case "${1:-patch}" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
    *) echo "Usage: $0 [major|minor|patch]"; exit 1 ;;
esac

NEW_VERSION="$MAJOR.$MINOR.$PATCH"

echo ""
echo "  $CURRENT_VERSION -> $NEW_VERSION"
echo ""
echo "Next steps:"
echo "  Update CHANGELOG.md and commit the release, then run:"
echo "  TELEMACHUS_RELEASE_CONFIRM=$NEW_VERSION ./scripts/release.sh $NEW_VERSION"
