#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$SCRIPT_DIR/android-env.sh"

echo "🔨 Building Android Client..."
cd "$ROOT_DIR/AndroidClient"
android_configure_build_env

./gradlew assembleDebug testDebugUnitTest

echo ""
echo "✅ Build successful!"
echo ""
echo "📦 APK: $ROOT_DIR/AndroidClient/app/build/outputs/apk/debug/app-debug.apk"
echo ""
echo "To install on device:"
echo "  adb install -r $ROOT_DIR/AndroidClient/app/build/outputs/apk/debug/app-debug.apk"
echo ""
echo "Or run: ./scripts/install_android.sh"
