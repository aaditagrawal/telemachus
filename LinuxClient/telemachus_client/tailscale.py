"""Tailscale peer discovery for the Linux client (stdlib only).

The client normally learns its Mac host from ``--qr`` pairing or the config
file. On a tailnet the Mac's address can change (DHCP, roam, MagicDNS
renumbering), so this module enumerates the nodes reported by
``tailscale status --json`` and probes the Telemachus listener port to find
one that is actually serving.

Everything here degrades to an empty result instead of raising when the CLI
is missing, logged out, stopped, or slow: the caller decides what to tell
the user. ``tailscale status --json`` carries no pair token, so nothing in
this module can leak one; no token is read, stored, or logged.

The CLI is invoked as plain ``tailscale`` (never ``sudo``). On many installs
``status`` only works for the daemon operator; if it exits non-zero the
caller gets an empty list plus a log line. Fix with::

    sudo tailscale set --operator="$USER"
"""

from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .protocol import DEFAULT_PORT

log = logging.getLogger("telemachus.client.tailscale")

TAILSCALE_BIN: str = "tailscale"
DEFAULT_STATUS_TIMEOUT: float = 5.0
DEFAULT_PROBE_TIMEOUT: float = 1.0
MAX_PROBE_WORKERS: int = 16


@dataclass(frozen=True)
class Peer:
    """One ``tailscale status`` peer, reduced to what discovery needs."""

    name: str
    ip: str
    dns: str = ""
    os: str = ""
    online: bool = False
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Accept any iterable of tags but always store an immutable tuple,
        # so a frozen Peer stays hashable and comparable.
        object.__setattr__(self, "tags", tuple(self.tags))


def tailscale_available() -> bool:
    """True when a ``tailscale`` executable is on PATH.

    This checks the CLI only, not daemon state; a stopped or logged-out
    daemon still reports "available" and surfaces as an empty
    :func:`list_peers` result.
    """
    return shutil.which(TAILSCALE_BIN) is not None


def parse_status(json_str: str) -> list[Peer]:
    """Parse ``tailscale status --json`` output into peers.

    Raises :class:`ValueError` when the text is not a JSON object, so callers
    can tell "unparseable" from "no peers". The local node (``Self``) is not
    a connection target and is skipped; peers missing ``HostName`` fall back
    to their DNS label, then to their IP. Entries with neither a name nor an
    address are dropped. ``ip`` prefers the first IPv4 ``TailscaleIPs``
    entry (IPv6 only when that is all there is), then ``AllowedIPs``.
    """
    try:
        raw: Any = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"tailscale status is not JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ValueError("tailscale status is not a JSON object")
    peers_raw: Any = raw.get("Peer")
    if not isinstance(peers_raw, dict):
        return []
    peers: list[Peer] = []
    for entry in peers_raw.values():
        if not isinstance(entry, dict):
            continue
        peer: Optional[Peer] = _peer_from_entry(entry)
        if peer is not None:
            peers.append(peer)
    return peers


def list_peers(timeout: float = DEFAULT_STATUS_TIMEOUT) -> list[Peer]:
    """Return peers from ``tailscale status --json`` (``[]`` on any failure).

    Never raises: a missing binary, a non-zero exit (logged out / stopped /
    not an operator), a timeout, or unparseable output all yield an empty
    list with a log line. Only the exit code and the first line of stderr
    are logged, never the full status body.
    """
    argv: list[str] = [TAILSCALE_BIN, "status", "--json"]
    effective_timeout: float = (
        timeout if timeout and timeout > 0 else DEFAULT_STATUS_TIMEOUT
    )
    try:
        proc: subprocess.CompletedProcess[str] = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
        )
    except FileNotFoundError:
        log.info("tailscale CLI not found on PATH; skipping peer discovery")
        return []
    except subprocess.TimeoutExpired:
        log.warning("tailscale status timed out after %ss", timeout)
        return []
    except OSError as exc:
        log.warning("could not run 'tailscale status': %s", exc)
        return []
    if proc.returncode != 0:
        first_stderr: str = ""
        for line in (proc.stderr or "").splitlines():
            if line.strip():
                first_stderr = line.strip()
                break
        log.warning(
            "tailscale status failed (rc=%d)%s",
            proc.returncode,
            f": {first_stderr}" if first_stderr else "",
        )
        return []
    try:
        return parse_status(proc.stdout or "")
    except ValueError as exc:
        log.warning("unparseable tailscale status: %s", exc)
        return []


def discover_hosts(
    peers: Iterable[Peer],
    port: int = DEFAULT_PORT,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
) -> list[Peer]:
    """Return the online peers that accept a TCP connection on ``port``.

    Peers are probed concurrently and the result preserves input order.
    Offline peers are filtered out (logically) before any socket is opened,
    so a sleeping laptop never costs a probe timeout.
    """
    if not 1 <= int(port) <= 65535:
        raise ValueError(f"port must be 1..65535, got {port}")
    candidates: list[Peer] = [p for p in peers if p.online and p.ip]
    if not candidates:
        return []
    effective_timeout: float = (
        probe_timeout if probe_timeout and probe_timeout > 0 else DEFAULT_PROBE_TIMEOUT
    )
    if len(candidates) == 1:
        reachable: Sequence[bool] = (
            _port_open(candidates[0].ip, port, effective_timeout),
        )
    else:
        with ThreadPoolExecutor(max_workers=min(MAX_PROBE_WORKERS, len(candidates))) as pool:
            reachable = list(
                pool.map(lambda p: _port_open(p.ip, port, effective_timeout), candidates)
            )
    return [peer for peer, ok in zip(candidates, reachable) if ok]


def _port_open(ip: str, port: int, timeout: float) -> bool:
    """True when a TCP connection to ``ip:port`` succeeds within ``timeout``."""
    if not ip:
        return False
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _peer_from_entry(entry: Mapping[str, Any]) -> Optional[Peer]:
    """Map one ``Peer`` JSON object to a :class:`Peer` (tolerant of gaps)."""
    ip: str = _primary_ip(entry.get("TailscaleIPs"))
    if not ip:
        ip = _primary_ip(entry.get("AllowedIPs"), strip_cidr=True)
    dns: str = _clean_dns(entry.get("DNSName"))
    name: str = str(entry.get("HostName") or "").strip()
    if not name and dns:
        name = dns.split(".")[0]
    if not name and not ip:
        return None
    tags_raw: Any = entry.get("Tags")
    tags: tuple[str, ...] = (
        tuple(str(t) for t in tags_raw) if isinstance(tags_raw, list) else ()
    )
    return Peer(
        name=name or ip,
        ip=ip,
        dns=dns,
        os=str(entry.get("OS") or ""),
        # Strict: only an explicit JSON true counts as online, so a schema
        # change degrades to "no candidate" rather than probing dead nodes.
        online=entry.get("Online") is True,
        tags=tags,
    )


def _primary_ip(value: Any, strip_cidr: bool = False) -> str:
    """First IPv4 address, else the first address, else ``""``."""
    if not isinstance(value, list):
        return ""
    addresses: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            continue
        addresses.append(item.split("/", 1)[0] if strip_cidr else item)
    if not addresses:
        return ""
    for address in addresses:
        if ":" not in address:
            return address
    return addresses[0]


def _clean_dns(value: Any) -> str:
    """Strip the trailing root dot from a MagicDNS name (``mac.ts.net.``)."""
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip(".")
