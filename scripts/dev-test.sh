#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/android-env.sh"
VERSION=$(cat "$ROOT_DIR/VERSION" | tr -d '[:space:]')
APP_DIR="$ROOT_DIR/Telemachus.app"

echo "======================================="
echo "  Telemachus - Dev Test (v$VERSION)"
echo "======================================="
echo ""

# 1. Build macOS
echo "[1/5] Building macOS..."
cd "$ROOT_DIR/MacHost"
swift build -c release 2>&1 | tail -3
echo "  OK"

# 2. Create .app bundle from the shared package metadata
echo "[2/5] Creating .app bundle..."
TELEMACHUS_BINARY="$ROOT_DIR/MacHost/.build/release/Telemachus" \
TELEMACHUS_SIGNING_IDENTITY=- \
TELEMACHUS_SKIP_DMG=1 \
    "$ROOT_DIR/scripts/package_mac.sh" >/dev/null
echo "  OK"

# 3. Build Android
echo "[3/5] Building Android..."
cd "$ROOT_DIR/AndroidClient"
android_configure_build_env
./gradlew assembleDebug -q
APK="$ROOT_DIR/AndroidClient/app/build/outputs/apk/debug/app-debug.apk"
echo "  OK"

# 4. Install APK on device
echo "[4/5] Installing APK..."
if ANDROID_SERIAL="$(adb_select_single_device 2>/dev/null)"; then
    export ANDROID_SERIAL
    adb_cmd install -r "$APK" 2>&1 | tail -1
else
    echo "  No device connected, skipping install"
fi

# 5. Run macOS app
echo "[5/5] Starting macOS app..."
pkill -x Telemachus 2>/dev/null || true
sleep 0.5

if [ -n "${ANDROID_SERIAL:-}" ]; then
    adb_cmd reverse --remove tcp:54321 2>/dev/null || true
    adb_cmd reverse tcp:54321 tcp:54321 2>/dev/null || true
fi
open "$APP_DIR"

echo ""
echo "======================================="
echo "  Ready to test!"
echo "  App: $APP_DIR"
echo "  Open Telemachus on your tablet"
echo "======================================="
echo ""
read -p "Test result? [y=OK / n=failed]: " RESULT

pkill -x Telemachus 2>/dev/null || true

if [ "$RESULT" = "y" ]; then
    echo ""
    echo "Test passed. Ready to push."
else
    echo ""
    echo "Test failed. Fix and re-run."
    exit 1
fi
