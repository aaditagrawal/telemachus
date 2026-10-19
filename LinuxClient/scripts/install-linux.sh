#!/usr/bin/env bash
# Install the Telemachus Linux client for the current user (no root, no sudo).
#
# Copies the client into ~/.local/share/telemachus and symlinks the launchers
# into ~/.local/bin, which is where the systemd unit's ExecStart points
# (%h/.local/bin/telemachus-linux). Idempotent: re-running replaces the copy
# and relinks.
#
# It does NOT install dependencies, enable the service, or pair the client.
# After this: `telemachus-linux --qr 'telemachus://...'` to pair, and the
# scout's "Install service" button to add the user unit.
set -euo pipefail

here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
src="$(dirname "$here")"                       # LinuxClient/
share="${XDG_DATA_HOME:-$HOME/.local/share}/telemachus"
bin="${XDG_BIN_HOME:-$HOME/.local/bin}"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found on PATH; install it first (apt install python3)." >&2
    exit 1
fi

# The launchers cd to their own directory, so the whole client (package,
# launchers, reference unit) moves together. __pycache__ is deliberately not
# copied; tests/ and requirements.txt are not needed at runtime.
install -d "$share" "$bin"
for item in telemachus_client telemachus-linux telemachus-scout systemd; do
    if [ ! -e "$src/$item" ]; then
        echo "missing $src/$item; run this script from the repo checkout." >&2
        exit 1
    fi
    rm -rf "$share/$item"
    cp -a "$src/$item" "$share/$item"
done
find "$share/telemachus_client" -name '__pycache__' -type d -prune -exec rm -rf {} +

ln -sfn "$share/telemachus-linux" "$bin/telemachus-linux"
ln -sfn "$share/telemachus-scout" "$bin/telemachus-scout"

echo "client:    $share"
echo "launchers: $bin/telemachus-linux, $bin/telemachus-scout"
case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "NOTE: $bin is not on PATH; add it in your shell profile and re-login." ;;
esac
echo "next:      telemachus-linux --qr 'telemachus://...'   # pair, then reuse"
echo "service:   'Install service' in telemachus-scout (~/.config/systemd/user)"
