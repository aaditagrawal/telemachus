#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APK_PATH="$ROOT_DIR/AndroidClient/app/build/outputs/apk/debug/app-debug.apk"
. "$SCRIPT_DIR/android-env.sh"

echo "📱 Installing Android app..."

# Check if APK exists
if [ ! -f "$APK_PATH" ]; then
    echo "❌ APK not found. Building first..."
    "$SCRIPT_DIR/build_android.sh"
fi

adb_require_single_device

# Install APK
if ! INSTALL_OUTPUT="$(adb_cmd install -r "$APK_PATH" 2>&1)"; then
    printf '%s\n' "$INSTALL_OUTPUT" >&2
    if printf '%s\n' "$INSTALL_OUTPUT" | grep -q INSTALL_FAILED_UPDATE_INCOMPATIBLE; then
        echo >&2
        echo "The installed app was signed with a different key." >&2
        echo "Uninstalling will delete Telemachus settings and wireless pairing." >&2
        echo "If that is acceptable, run:" >&2
        echo "  adb -s $ANDROID_SERIAL uninstall dev.telemachus.display" >&2
        echo "Then run this installer again." >&2
    fi
    exit 1
fi
printf '%s\n' "$INSTALL_OUTPUT"

echo ""
echo "✅ App installed successfully!"
echo ""
echo "📲 Setting up USB port forwarding..."
adb_cmd reverse --remove tcp:54321 2>/dev/null || true
adb_cmd reverse tcp:54321 tcp:54321

echo "✅ Port 54321 forwarded"
adb_cmd shell am start -a android.intent.action.MAIN \
    -n dev.telemachus.display/.MainActivity \
    --ez auto_connect true >/dev/null
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Ready! Telemachus was launched on your Android device"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
