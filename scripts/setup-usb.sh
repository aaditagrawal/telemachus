#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/android-env.sh"

echo "🔧 Setting up USB port forwarding..."

if ! adb_require_single_device; then
    echo "❌ No unique authorized Android device found via ADB"
    echo ""
    echo "Troubleshooting:"
    echo "  1. Connect device via USB cable"
    echo "  2. Enable Developer Options on device"
    echo "  3. Enable USB Debugging in Developer Options"
    echo "  4. Accept the USB debugging prompt on device"
    echo "  5. Run this script again"
    exit 1
fi

echo "  ✓ Device connected"

# Remove only Telemachus's reverse mapping. Other development tools may own
# additional mappings on the same device.
echo "  Clearing Telemachus's existing port forward..."
adb_cmd reverse --remove tcp:54321 2>/dev/null || true

# Setup new reverse
echo "  Setting up port 54321..."
adb_cmd reverse tcp:54321 tcp:54321

# Verify
if adb_cmd reverse --list | grep -q "tcp:54321"; then
    echo ""
    echo "✅ USB port forwarding active!"
    echo ""
    adb_cmd reverse --list
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Ready to connect. Make sure Mac app is running."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    echo "❌ Port forwarding failed"
    exit 1
fi
