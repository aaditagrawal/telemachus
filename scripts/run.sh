#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/android-env.sh"

echo "🚀 Starting Telemachus..."

# Kill any existing instance
pkill -x Telemachus 2>/dev/null || true
sleep 0.3

# AppKit must launch from an application bundle so permissions and identity are
# stable across runs.
if [ ! -d "$ROOT_DIR/Telemachus.app" ]; then
    echo "❌ No build found. Building now..."
    "$SCRIPT_DIR/build_mac.sh"
fi
echo "  Opening Telemachus.app..."
open "$ROOT_DIR/Telemachus.app"

echo ""
echo "✅ Mac app started!"
echo ""

# Setup USB if device connected
if ANDROID_SERIAL="$(adb_select_single_device 2>/dev/null)"; then
    export ANDROID_SERIAL
    echo "📱 Android device detected, setting up USB..."
    adb_cmd reverse --remove tcp:54321 2>/dev/null || true
    adb_cmd reverse tcp:54321 tcp:54321
    adb_cmd shell am start -a android.intent.action.MAIN \
        -n dev.telemachus.display/.MainActivity \
        --ez auto_connect true >/dev/null 2>&1 || true
    echo "  ✓ Port forwarding ready"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Open 'Telemachus' on Android and tap Connect"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
