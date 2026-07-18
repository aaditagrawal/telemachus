#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/capture-cadence-benchmark.swift"
BINARY="${TMPDIR:-/tmp}/telemachus-capture-cadence-benchmark"
ARCH="$(uname -m)"

xcrun swiftc \
    -target "${ARCH}-apple-macosx13.0" \
    -O \
    "$SOURCE" \
    -o "$BINARY"

"$BINARY" "$@"
