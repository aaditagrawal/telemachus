"""Tkinter scout: pick a Tailscale host and launch the client (stdlib only).

:func:`run_scout` opens a small window listing the online Tailscale peers that
accept a TCP connection on the Telemachus port (reusing
:mod:`telemachus_client.tailscale`), lets the user pick one, and starts
``python3 -m telemachus_client`` for it as a subprocess. When the config file
already carries a token the child inherits it; otherwise the scout asks for the
``telemachus://`` pairing URI from the Mac, saves the pairing (host overridden
with the selected peer's Tailscale address), and launches the child against the
saved config.

Two more buttons manage the user service: **Install service** writes
``~/.config/systemd/user/telemachus-linux.service`` and runs
``systemctl --user daemon-reload`` (it never enables the unit), and **Start
service now** runs ``systemctl --user start``.

Only the standard library is used (tkinter, subprocess, threading, queue).
tkinter is imported defensively: on a host without ``python3-tk`` or without a
display, :func:`run_scout` reports the problem and returns 1 instead of
raising, so importing this module (and ``--scout``) never hard-crashes.
"""

from __future__ import annotations

import base64
import binascii
import os
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .protocol import DEFAULT_PORT

try:  # pragma: no cover - exercised only on hosts without python3-tk
    import tkinter as tk
    from tkinter import messagebox, simpledialog
    from tkinter import ttk

    _TK_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as exc:  # pragma: no cover
    tk = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    simpledialog = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    _TK_IMPORT_ERROR = exc

# ``python3 -m telemachus_client`` must be importable from the child, so run it
# with the same interpreter and point cwd/PYTHONPATH at the client root (the
# parent of the package directory) regardless of where the scout was started.
PYTHON_BIN: str = sys.executable or "python3"
_PACKAGE_DIR: Path = Path(__file__).resolve().parent
_PROJECT_DIR: Path = _PACKAGE_DIR.parent

WINDOW_TITLE: str = "Telemachus — Tailscale scout"

# -- systemd user service ------------------------------------------------------
# The unit is written into the user manager's directory by the "Install
# service" button; it is never enabled automatically. The reference copy lives
# at LinuxClient/systemd/telemachus-linux.service and a test keeps the two in
# sync. ExecStart is the launcher that scripts/install-linux.sh symlinks into
# ~/.local/bin, addressed through systemd's %h specifier so the unit carries no
# absolute home path.
SERVICE_NAME: str = "telemachus-linux.service"
SYSTEMD_USER_UNIT_DIR: Path = Path.home() / ".config" / "systemd" / "user"
LAUNCHER_RELPATH: str = ".local/bin/telemachus-linux"
_SYSTEMCTL_TIMEOUT: float = 30.0

# -- SSH authorized_keys (Linux side) -----------------------------------------
# The Mac connects over SSH with its own key; the Linux host authorizes it by
# appending a restricted entry to ~/.ssh/authorized_keys. The "Authorize Mac"
# button in the scout performs that append from a pasted public key so no
# terminal commands are needed.
SSH_SERVICE_COMMAND: str = "/usr/bin/systemctl --user start telemachus-linux.service"
_AUTHORIZED_KEYS_MODE: int = 0o600


def systemd_unit_text() -> str:
    """The user unit as written by the scout's Install button.

    Fixed ExecStart/PATH plus the Wayland/X11 variables a user unit started
    outside a login session (autostart, ssh) cannot inherit. ``%h`` is the
    user's home and ``%t`` the runtime dir, so nothing here is machine-
    specific.
    """
    return (
        "[Unit]\n"
        "Description=Telemachus Linux client (Mac display receiver)\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=%h/{LAUNCHER_RELPATH}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "# The unit can start outside a graphical login session (ssh, autostart),\n"
        "# where these are not inherited. Adjust WAYLAND_DISPLAY if the compositor\n"
        "# names its socket differently (GNOME/Wayland uses wayland-0).\n"
        f"Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n"
        "Environment=XDG_RUNTIME_DIR=%t\n"
        "Environment=WAYLAND_DISPLAY=wayland-0\n"
        "Environment=DISPLAY=:0\n"
        "\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n"
    )


def launcher_path() -> Path:
    """Where scripts/install-linux.sh puts the client launcher."""
    return Path.home() / LAUNCHER_RELPATH


def install_user_unit(unit_dir: Optional[Path] = None) -> Path:
    """Write the user unit and reload the user manager.

    Never enables or starts anything: enabling is a deliberate
    ``systemctl --user enable`` the user runs. Returns the unit path.
    """
    target_dir = Path(unit_dir) if unit_dir is not None else SYSTEMD_USER_UNIT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    unit_path = target_dir / SERVICE_NAME
    unit_path.write_text(systemd_unit_text(), encoding="utf-8")
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=False,
        capture_output=True,
        text=True,
        timeout=_SYSTEMCTL_TIMEOUT,
    )
    return unit_path


def start_user_service() -> "subprocess.CompletedProcess[str]":
    """Start the installed unit now (``systemctl --user start``)."""
    return subprocess.run(
        ["systemctl", "--user", "start", SERVICE_NAME],
        check=False,
        capture_output=True,
        text=True,
        timeout=_SYSTEMCTL_TIMEOUT,
    )


def authorized_keys_path() -> Path:
    """Default location of the user's authorized_keys file."""
    return Path.home() / ".ssh" / "authorized_keys"


_KEY_TYPES: frozenset = frozenset(
    {
        "ssh-rsa",
        "ssh-dss",
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
)


def _ssh_field(text: str) -> tuple[str, str]:
    """Split a field, allowing spaces inside double-quoted SSH options."""
    quoted = escaped = False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char in " \t" and not quoted:
            return text[:i], text[i:].lstrip(" \t")
    if quoted:
        raise ValueError("unterminated quote in public key options")
    return text, ""


def _parse_key_line(text: str) -> tuple[str, str, bytes]:
    """Return options, the public key text, and its decoded SSH key blob."""
    if any(ord(char) < 32 and char != "\t" for char in text) or "\x7f" in text:
        raise ValueError("paste exactly one public key on a single line")
    text = text.strip()
    if not text or text.startswith("#"):
        raise ValueError("a supported SSH public key is required")
    first, rest = _ssh_field(text)
    options = ""
    if first not in _KEY_TYPES:
        options = first
        first, rest = _ssh_field(rest)
    if first not in _KEY_TYPES or not rest:
        raise ValueError("a supported SSH public key is required")
    fields = rest.split(None, 1)
    encoded = fields[0]
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("invalid SSH public key encoding") from None
    # SSH blobs begin with a length-prefixed algorithm name. Reject mismatched
    # types and truncated/fake blobs instead of accepting arbitrary base64 text.
    algorithm = first.encode("ascii")
    header = len(algorithm).to_bytes(4, "big") + algorithm
    if not blob.startswith(header) or len(blob) <= len(header) + 4:
        raise ValueError("invalid SSH public key blob")
    if first == "ssh-ed25519" and (
        blob[len(header):len(header) + 4] != (32).to_bytes(4, "big")
        or len(blob) != len(header) + 4 + 32
    ):
        raise ValueError("invalid Ed25519 public key blob")
    comment = " " + fields[1] if len(fields) == 2 else ""
    return options, f"{first} {encoded}{comment}", blob


def _check_key_options(options: str) -> None:
    """Accept only redundant restrictions and the service command's argv."""
    restrictions = {
        "restrict", "no-agent-forwarding", "no-port-forwarding", "no-pty",
        "no-user-rc", "no-x11-forwarding",
    }
    command_seen = False
    for option in options.split(",") if options else ():
        if option.lower() in restrictions:
            continue
        name, _, value = option.partition("=")
        if name.lower() == "command" and not command_seen:
            try:
                # Decode the authorized_keys double-quoted value, then parse
                # the shell command it carries. Never execute pasted text.
                quoted = shlex.split(value)
                command = shlex.split(quoted[0]) if len(quoted) == 1 else []
            except ValueError:
                command = []
            expected = shlex.split(SSH_SERVICE_COMMAND)
            with_separator = expected[:-1] + ["--", expected[-1]]
            if (not value.startswith('"') or not value.endswith('"')
                    or command not in (expected, with_separator)):
                raise ValueError("the forced command must start the Telemachus service")
            command_seen = True
            continue
        raise ValueError("unsupported or unsafe public key options")


def authorize_mac(key_text: str, *, path: Optional[Path] = None) -> Path:
    """Authorize one key only to start the service, with forwarding disabled.

    A full authorized_keys line may contain redundant restrictions and the
    service command, optionally with shell quoting and ``--`` before the unit.
    Other options are rejected. Matching existing keys
    are consolidated into the restricted entry; unrelated lines survive.
    Read failures abort the update. Replacement is atomic and mode 0600 from
    creation; a newly created parent directory is mode 0700.
    """
    options, public_key, blob = _parse_key_line(key_text)
    _check_key_options(options)
    line = f'restrict,command="{SSH_SERVICE_COMMAND}" {public_key}\n'
    ak_path = Path(path) if path is not None else authorized_keys_path()
    if ak_path.is_symlink():
        raise ValueError("authorized_keys must not be a symbolic link")
    try:
        existing = ak_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""

    lines: list[str] = []
    replaced = False
    for old_line in existing.splitlines(keepends=True):
        try:
            old_options, _, old_blob = _parse_key_line(old_line.rstrip("\r\n"))
        except ValueError:
            lines.append(old_line)
            continue
        if old_blob != blob:
            lines.append(old_line)
            continue
        # Do not remove pre-existing source restrictions or replace another
        # forced command without telling the user that the key is already used.
        _check_key_options(old_options)
        if not replaced:
            lines.append(line)
            replaced = True
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(line)

    ak_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".authorized_keys.", dir=ak_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), _AUTHORIZED_KEYS_MODE)
            handle.write("".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, ak_path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return ak_path


def save_pairing(uri: str, host: str, path: Optional[Path] = None) -> tuple[str, int]:
    """Persist a pairing URI for ``host``; return the ``(host, port)`` to use.

    The URI carries the Mac's own address (its LAN IP or MagicDNS name). Once
    the user has picked a peer from the Tailscale list, that peer's address is
    the one to connect to, so ``host`` overrides the URI host. Port and token
    come from the URI; the device name is NOT persisted, so it defaults to the
    local hostname.

    Raises :class:`ValueError` for a malformed URI, a missing ``host``, or a
    config that cannot be written.
    """
    from .config import save_config  # noqa: PLC0415
    from .protocol import ProtocolError, parse_qr  # noqa: PLC0415

    if not host:
        raise ValueError("host is required to save a pairing")
    try:
        info = parse_qr(uri)
    except ProtocolError as exc:
        raise ValueError(str(exc)) from None
    port = int(info.port)
    save_config(
        {
            "host": host,
            "port": port,
            "token": bytes(info.token),
        },
        path=path,
    )
    return host, port


@dataclass(frozen=True)
class ScanResult:
    """Outcome of one discovery scan (hosts, a user-facing message, success)."""

    hosts: list = field(default_factory=list)
    message: str = ""
    ok: bool = False


def saved_config() -> dict:
    """Return the file config (``{}`` when absent or unreadable)."""
    try:
        from .config import load_config  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001 - a bad config must not break the GUI
        return {}
    return cfg if isinstance(cfg, dict) else {}


def config_port(cfg: Optional[dict] = None) -> int:
    """Port to probe/connect with: the saved config value, else the default."""
    cfg = saved_config() if cfg is None else cfg
    try:
        port = int(cfg.get("port", DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def has_saved_token(cfg: Optional[dict] = None) -> bool:
    """True when the config file carries a non-empty token."""
    cfg = saved_config() if cfg is None else cfg
    return bool(str(cfg.get("token", "") or "").strip())


def scan_hosts(port: int = DEFAULT_PORT) -> ScanResult:
    """List online peers accepting TCP on ``port``; never raises.

    Missing CLI, no online peers, and no listener all come back as a failed
    :class:`ScanResult` with a message the status label can show.
    """
    from . import tailscale  # noqa: PLC0415

    if not tailscale.tailscale_available():
        return ScanResult(
            message="tailscale CLI not found on PATH. Install Tailscale or "
                    "connect with --host.",
            ok=False,
        )
    try:
        peers = [p for p in tailscale.list_peers() if p.online]
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        return ScanResult(message=f"Tailscale lookup failed: {exc}", ok=False)
    if not peers:
        return ScanResult(message="No online Tailscale peers found.", ok=False)
    try:
        hosts = tailscale.discover_hosts(peers, port=port)
    except Exception as exc:  # noqa: BLE001
        return ScanResult(message=f"Port probe failed: {exc}", ok=False)
    if not hosts:
        return ScanResult(
            message=f"Found {len(peers)} online peer(s), but none accept TCP "
                    f"on port {port}.",
            ok=False,
        )
    return ScanResult(
        hosts=list(hosts),
        message=f"Found {len(hosts)} host(s) with port {port} open.",
        ok=True,
    )


def describe_peer(peer: object) -> str:
    """Listbox line for a peer: ``name  ip (os, dns)``."""
    name = str(getattr(peer, "name", "") or getattr(peer, "ip", ""))
    ip = str(getattr(peer, "ip", "") or "")
    detail = ", ".join(
        part
        for part in (str(getattr(peer, "os", "") or ""), str(getattr(peer, "dns", "") or ""))
        if part
    )
    return f"{name}  {ip}" + (f" ({detail})" if detail else "")


def build_launch_argv(
    *, host: Optional[str] = None, port: Optional[int] = None, qr: Optional[str] = None
) -> list[str]:
    """Build a child command, retaining explicit host/port overrides for QR."""
    argv: list[str] = [PYTHON_BIN, "-m", "telemachus_client"]
    if not host and qr is None:
        raise ValueError("host is required when no --qr URI is given")
    if qr is not None:
        argv += ["--qr", qr]
    if host:
        argv += ["--host", host]
    if port is not None or qr is None:
        argv += ["--port", str(port if port is not None else DEFAULT_PORT)]
    return argv


def launch_client(
    *, host: Optional[str] = None, port: Optional[int] = None, qr: Optional[str] = None
) -> subprocess.Popen:
    """Start the client in its own session so it outlives the scout window."""
    argv = build_launch_argv(host=host, port=port, qr=qr)
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(_PROJECT_DIR) + (os.pathsep + existing if existing else "")
    return subprocess.Popen(argv, cwd=str(_PROJECT_DIR), env=env, start_new_session=True)


def run_scout(port: Optional[int] = None) -> int:
    """Open the scout window; return a process exit code when it closes."""
    if tk is None:
        print(
            "scout needs tkinter (apt install python3-tk): "
            f"{_TK_IMPORT_ERROR}",
            file=sys.stderr,
        )
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"scout: cannot open a display ({exc}).", file=sys.stderr)
        return 1
    ScoutApp(root, port=port if port is not None else config_port())
    root.mainloop()
    return 0


class ScoutApp:
    """Scout window: peer list, Refresh/Connect, status label."""

    def __init__(self, root: "tk.Tk", *, port: int = DEFAULT_PORT) -> None:
        self.root = root
        self.port = port
        self.hosts: list = []
        self._results: "queue.Queue[ScanResult]" = queue.Queue()

        root.title(WINDOW_TITLE)
        root.minsize(480, 320)

        frame = ttk.Frame(root, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"Tailscale hosts with port {self.port} open:").pack(
            anchor="w"
        )

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True, pady=(6, 8))
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.listbox = tk.Listbox(
            list_frame, yscrollcommand=scrollbar.set, activestyle="dotbox"
        )
        scrollbar.config(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda _event: self.on_connect())

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.refresh_button = ttk.Button(buttons, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.connect_button = ttk.Button(
            buttons, text="Connect", command=self.on_connect
        )
        self.connect_button.pack(side="left", padx=(8, 0))
        self.install_button = ttk.Button(
            buttons, text="Install service", command=self.on_install_service
        )
        self.install_button.pack(side="left", padx=(8, 0))
        self.start_button = ttk.Button(
            buttons, text="Start service now", command=self.on_start_service
        )
        self.start_button.pack(side="left", padx=(8, 0))
        self.authorize_button = ttk.Button(
            buttons, text="Authorize Mac", command=self.on_authorize_mac
        )
        self.authorize_button.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(frame, textvariable=self.status_var, wraplength=440).pack(
            anchor="w", pady=(8, 0)
        )

        self.root.after(150, self._drain_results)
        self.refresh()

    # -- status / list plumbing -------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.refresh_button.config(state=state)
        self.connect_button.config(state=state)
        self.install_button.config(state=state)
        self.start_button.config(state=state)
        self.authorize_button.config(state=state)

    def _drain_results(self) -> None:
        # Single poller scheduled once at construction (never re-scheduled by
        # refresh()), so pressing Refresh cannot stack drain callbacks.
        try:
            result = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._apply_result(result)
        self.root.after(150, self._drain_results)

    def _apply_result(self, result: ScanResult) -> None:
        self.hosts = result.hosts
        self.listbox.delete(0, "end")
        for peer in result.hosts:
            self.listbox.insert("end", describe_peer(peer))
        if result.hosts:
            self.listbox.selection_set(0)
        self._set_status(result.message)
        self._set_busy(False)

    # -- actions ----------------------------------------------------------------
    def refresh(self) -> None:
        self._set_busy(True)
        self._set_status(f"Scanning Tailscale for port {self.port}…")
        threading.Thread(target=self._scan_worker, name="scout-scan", daemon=True).start()

    def _scan_worker(self) -> None:
        self._results.put(scan_hosts(self.port))

    def on_connect(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo(
                "Telemachus", "Select a host to connect to.", parent=self.root
            )
            return
        peer = self.hosts[selection[0]]
        ip = str(getattr(peer, "ip", "") or "")
        if not ip:
            self._set_status("The selected peer has no address; refresh and retry.")
            return
        if has_saved_token():
            from .config import save_config  # noqa: PLC0415

            note = ""
            try:
                save_config({"host": ip, "port": self.port})
            except (OSError, ValueError):
                note = " (address not saved; service will use its previous address)"
            self._launch(host=ip, port=self.port, note=note)
            return
        uri = self._prompt_uri()
        if uri is None:
            return
        try:
            host, port = save_pairing(uri, ip)
        except (OSError, ValueError):
            # Persisting failed: still connect, handing the child the URI so
            # this session is paired; nothing is stored for the next one.
            self._launch(host=ip, qr=uri, note=" (pairing not saved)")
            return
        self._launch(host=host, port=port, note=" (paired; token saved)")

    def on_install_service(self) -> None:
        """Write the user unit + daemon-reload; never enable or start it."""
        try:
            unit_path = install_user_unit()
        except (OSError, subprocess.SubprocessError) as exc:
            self._set_status(f"Could not install the service: {exc}")
            return
        note = ""
        if not launcher_path().exists():
            note = (f" — {launcher_path()} is missing; run "
                    "LinuxClient/scripts/install-linux.sh first")
        self._set_status(
            f"Installed {unit_path}{note}. Start it with the button below, or: "
            f"systemctl --user enable --now {SERVICE_NAME}"
        )

    def on_start_service(self) -> None:
        """Start the installed unit now (it keeps running without the scout)."""
        try:
            proc = start_user_service()
        except (OSError, subprocess.SubprocessError) as exc:
            self._set_status(f"Could not start the service: {exc}")
            return
        if proc.returncode == 0:
            self._set_status(f"Started {SERVICE_NAME}.")
            return
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        self._set_status(
            f"systemctl start failed (rc={proc.returncode})"
            + (f": {detail[0]}" if detail else "")
        )

    def on_authorize_mac(self) -> None:
        '''Paste a Mac public key and append it to authorized_keys.'''
        text = simpledialog.askstring(
            "Authorize Mac",
            "Paste the Mac's public key (ssh-ed25519 ... telemachus-mac) "
            "or a full authorized_keys line:",
            parent=self.root,
        )
        if text is None or not text.strip():
            self._set_status("Authorization cancelled; no key added.")
            return
        try:
            ak_path = authorize_mac(text)
        except (OSError, ValueError) as exc:
            self._set_status(f"Could not authorize Mac: {exc}")
            return
        self._set_status(f"Authorized Mac key in {ak_path}.")

    def _prompt_uri(self) -> Optional[str]:
        """Ask for the Mac's pairing URI until it parses or the user cancels."""
        from .protocol import ProtocolError, parse_qr  # noqa: PLC0415

        while True:
            uri = simpledialog.askstring(
                "Pair with the Mac",
                "No token is saved yet. Paste the telemachus:// pairing URI "
                "from the Mac:",
                parent=self.root,
            )
            if uri is None or not uri.strip():
                self._set_status("Pairing cancelled; no token saved.")
                return None
            uri = uri.strip()
            try:
                parse_qr(uri)
            except (ProtocolError, ValueError) as exc:
                messagebox.showerror("Invalid pairing URI", str(exc), parent=self.root)
                continue
            return uri

    def _launch(self, *, host: Optional[str] = None, port: Optional[int] = None,
                qr: Optional[str] = None, note: str = "") -> None:
        try:
            launch_client(host=host, port=port, qr=qr)
        except OSError as exc:
            self._set_status(f"Could not start the client: {exc}")
            messagebox.showerror(
                "Telemachus", f"Could not start the client:\n{exc}", parent=self.root
            )
            return
        target = host or "the paired host"
        if port is not None:
            target += f":{port}"
        self._set_status(f"Launched client for {target}{note}; closing scout.")
        self.root.after(150, self.root.destroy)


# -- convenience entry point (``python3 -m telemachus_client.scout``) ---------
def main() -> int:
    return run_scout()


if __name__ == "__main__":
    raise SystemExit(main())
