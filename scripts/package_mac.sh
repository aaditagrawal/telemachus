#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")"
BINARY="${TELEMACHUS_BINARY:-$ROOT_DIR/MacHost/.build/release-universal/Telemachus}"
APP_DIR="$ROOT_DIR/Telemachus.app"
SIGNING_IDENTITY="${TELEMACHUS_SIGNING_IDENTITY:--}"
ARTIFACT_SUFFIX="${TELEMACHUS_ARTIFACT_SUFFIX:-mac-universal-unsigned-source-build}"
DMG_PATH="$ROOT_DIR/Telemachus-${VERSION}-${ARTIFACT_SUFFIX}.dmg"
SKIP_DMG="${TELEMACHUS_SKIP_DMG:-0}"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "VERSION must be a semantic version, got '$VERSION'." >&2
    exit 1
fi
if [ ! -x "$BINARY" ]; then
    echo "Missing package binary: $BINARY" >&2
    exit 1
fi
if [ "$SIGNING_IDENTITY" = "-" ] &&
   [ "$ARTIFACT_SUFFIX" != "mac-universal-unsigned-source-build" ]; then
    echo "Ad-hoc signed packages must be labeled unsigned-source-build." >&2
    exit 1
fi

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" \
    "$APP_DIR/Contents/Resources/Legal/licenses"
cp "$BINARY" "$APP_DIR/Contents/MacOS/Telemachus"
cp "$ROOT_DIR/MacHost/Info.plist" "$APP_DIR/Contents/Info.plist"
cp "$ROOT_DIR/LICENSE" "$APP_DIR/Contents/Resources/Legal/LICENSE"
cp "$ROOT_DIR/NOTICE" "$APP_DIR/Contents/Resources/Legal/NOTICE"
cp "$ROOT_DIR/THIRD_PARTY_NOTICES.md" \
   "$APP_DIR/Contents/Resources/Legal/THIRD_PARTY_NOTICES.md"
cp "$ROOT_DIR/PRIVACY.md" "$APP_DIR/Contents/Resources/Legal/PRIVACY.md"
cp "$ROOT_DIR/licenses/Apache-2.0.txt" \
   "$APP_DIR/Contents/Resources/Legal/licenses/Apache-2.0.txt"
cp "$ROOT_DIR/MacHost/Resources/Credits.html" \
   "$APP_DIR/Contents/Resources/Credits.html"

/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" \
    "$APP_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" \
    "$APP_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
    "Set :CFBundleGetInfoString Telemachus $VERSION — a fork of SideScreen" \
    "$APP_DIR/Contents/Info.plist"

if [ -f "$ROOT_DIR/MacHost/Resources/AppIcon.icns" ]; then
    cp "$ROOT_DIR/MacHost/Resources/AppIcon.icns" "$APP_DIR/Contents/Resources/"
    /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" \
        "$APP_DIR/Contents/Info.plist" 2>/dev/null ||
        /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" \
            "$APP_DIR/Contents/Info.plist"
fi

if [ "$SIGNING_IDENTITY" = "-" ]; then
    codesign --force --deep --sign - \
        --entitlements "$ROOT_DIR/MacHost/Telemachus.entitlements" \
        "$APP_DIR"
else
    codesign --force --deep --options runtime --timestamp \
        --sign "$SIGNING_IDENTITY" \
        --entitlements "$ROOT_DIR/MacHost/Telemachus.entitlements" \
        "$APP_DIR"
fi
codesign --verify --deep --strict --verbose=2 "$APP_DIR"
plutil -lint "$APP_DIR/Contents/Info.plist"

if [ "$SKIP_DMG" = "1" ]; then
    echo "$APP_DIR"
    exit 0
fi

DMG_DIR="$(mktemp -d)"
trap 'rm -rf "$DMG_DIR"' EXIT
cp -R "$APP_DIR" "$DMG_DIR/"
ln -s /Applications "$DMG_DIR/Applications"
hdiutil create -volname "Telemachus" -srcfolder "$DMG_DIR" \
    -ov -format UDZO "$DMG_PATH"

if [ "$SIGNING_IDENTITY" != "-" ]; then
    codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG_PATH"
    codesign --verify --verbose=2 "$DMG_PATH"
fi

echo "$DMG_PATH"
