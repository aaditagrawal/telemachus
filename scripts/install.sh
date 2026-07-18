#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ADB="${ADB:-adb}"
PORT=54321
. "$SCRIPT_DIR/android-env.sh"

echo "Installing Telemachus..."

if ! adb_require_single_device; then
    echo "No authorized Android device was found."
    echo "Connect the tablet, enable USB debugging, and accept its authorization prompt."
    exit 1
fi

"$SCRIPT_DIR/build_mac.sh"
"$SCRIPT_DIR/build_android.sh"

APK="$ROOT_DIR/AndroidClient/app/build/outputs/apk/debug/app-debug.apk"
if ! INSTALL_OUTPUT="$(adb_cmd install -r "$APK" 2>&1)"; then
    printf '%s\n' "$INSTALL_OUTPUT" >&2
    if printf '%s\n' "$INSTALL_OUTPUT" | grep -q INSTALL_FAILED_UPDATE_INCOMPATIBLE; then
        echo "The installed app uses a different signing key." >&2
        echo "Uninstalling deletes settings and pairing: adb -s $ANDROID_SERIAL uninstall dev.telemachus.display" >&2
    fi
    exit 1
fi
printf '%s\n' "$INSTALL_OUTPUT"
adb_cmd reverse --remove "tcp:$PORT" 2>/dev/null || true
adb_cmd reverse "tcp:$PORT" "tcp:$PORT"

open "$ROOT_DIR/Telemachus.app"
adb_cmd shell am start -a android.intent.action.MAIN \
    -n dev.telemachus.display/.MainActivity \
    --ez auto_connect true >/dev/null

echo
echo "Telemachus is installed and the USB client has been launched."
echo "One-time Mac permissions: Screen Recording, plus Accessibility for touch."
