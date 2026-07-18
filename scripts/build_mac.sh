#!/bin/bash
set -e

# Get absolute path to root directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Read version
VERSION=$(cat "$ROOT_DIR/VERSION" | tr -d '[:space:]')
echo "Building version $VERSION..."

cd "$ROOT_DIR/MacHost"

# Kill running instance
echo "Stopping running Telemachus..."
pkill -x Telemachus 2>/dev/null || true
sleep 0.5

# Clean old build
echo "Cleaning old build..."
rm -rf .build

# Build fresh (Universal Binary: arm64 + x86_64)
echo "Building macOS Host (arm64)..."
swift build -c release --arch arm64

echo "Building macOS Host (x86_64)..."
swift build -c release --arch x86_64

echo "Creating Universal Binary..."
mkdir -p ".build/release-universal"
lipo -create \
  .build/arm64-apple-macosx/release/Telemachus \
  .build/x86_64-apple-macosx/release/Telemachus \
  -output .build/release-universal/Telemachus

echo "Packaging ad-hoc signed source build..."
TELEMACHUS_SIGNING_IDENTITY=- \
TELEMACHUS_ARTIFACT_SUFFIX=mac-universal-unsigned-source-build \
    "$ROOT_DIR/scripts/package_mac.sh"

echo ""
echo "Build successful!"
echo "App: $ROOT_DIR/Telemachus.app"
echo "To run: open $ROOT_DIR/Telemachus.app"
