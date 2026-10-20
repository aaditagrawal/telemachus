#!/usr/bin/env bash
set -euo pipefail

VERSION="0.65.1"
SHA256="c1e429b0599cf1b516f369a2d9ec04eaf0e436f3c12b637df8851fa52ff694d0"
ARCHIVE="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/telemachus-swiftlint-${VERSION}.zip"
INSTALL_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/telemachus-swiftlint-${VERSION}"

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --output "$ARCHIVE" \
  "https://github.com/realm/SwiftLint/releases/download/${VERSION}/portable_swiftlint.zip"

printf '%s  %s\n' "$SHA256" "$ARCHIVE" | shasum -a 256 --check
mkdir -p "$INSTALL_DIR"
ditto -x -k "$ARCHIVE" "$INSTALL_DIR"
chmod 0755 "$INSTALL_DIR/swiftlint"

if [[ -n "${GITHUB_PATH:-}" ]]; then
  printf '%s\n' "$INSTALL_DIR" >> "$GITHUB_PATH"
else
  printf '%s\n' "$INSTALL_DIR/swiftlint"
fi
