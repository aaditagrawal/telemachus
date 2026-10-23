"""Telemachus Linux client entrypoint (Zorin OS / Ubuntu 24.04, Wayland).

System layout: Python owns the protocol (:mod:`telemachus_client.netclient`)
and the SDL2 window (:mod:`telemachus_client.display`); a system ``ffmpeg``
subprocess owns the decode (:mod:`telemachus_client.decode`), fed Annex-B
over stdin and read back as raw NV12 over stdout.

Only stdlib + pysdl2 are used here. pysdl2 and the sibling
``protocol``/``config``/``netclient`` modules are imported lazily so that
``--help`` (and ``py_compile``) work on machines without them.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import logging
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger("telemachus.client")

DEFAULT_PORT: int = 54321
DEFAULT_NAME: str = "linux-client"
PAIRING_EXECUTABLE: str = "./telemachus-linux"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser (no sibling imports; safe without pysdl2)."""
    p = argparse.ArgumentParser(
        prog="telemachus-linux",
        description=(
            "Telemachus Linux client: low-latency Mac display receiver "
            "(ffmpeg decode + SDL2 display)."
        ),
    )
    p.add_argument("--qr", default=None,
                   help="telemachus://HOST:PORT?t=TOKEN&name=NAME pairing URI from the Mac")
    p.add_argument("--host", default=None, help="Mac host address")
    p.add_argument("--port", type=int, default=None, help=f"Mac port (default {DEFAULT_PORT})")
    p.add_argument("--token-b64url", default=None,
                   help="32-byte bearer token, base64url (no padding required)")
    p.add_argument("--name", default=None, help="client device name")
    p.add_argument("--decoder", default=None, choices=["auto", "vaapi", "cuda", "sw"],
                   help="ffmpeg acceleration path (default auto: vaapi -> cuda -> sw)")
    p.add_argument("--display", default=None,
                   help="target monitor: index (e.g. 1), name substring (e.g. HP), "
                        "or WxH@Hz (e.g. 1920x1080@60). Default: the 1920x1080@60 display.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--fullscreen", dest="fullscreen", action="store_true", default=None,
                   help="fullscreen on the target monitor (default)")
    g.add_argument("--windowed", dest="fullscreen", action="store_false",
                   help="run in a window instead of fullscreen")
    p.add_argument("--no-input", action="store_true", default=None,
                   help="do not forward mouse as touch")
    p.add_argument("--hud", action="store_true",
                   help="show fps / Mbps / RTT overlay (toggle at runtime with F1)")
    p.add_argument("--discover-tailscale", action="store_true",
                   help="list online Tailscale peers with the Telemachus port open "
                        "and prompt for one (requires the tailscale CLI)")
    p.add_argument("--scout", action="store_true",
                   help="open the tkinter Tailscale scout to pick a host and launch "
                        "the client (requires python3-tk)")
    p.add_argument("--selftest", action="store_true",
                   help="serve mockserver.MockTelemachusServer on loopback and point "
                        "the client at it (no Mac needed)")
    p.add_argument("--fake-payload", action="store_true",
                   help="exercise decode/display with synthetic frames instead of "
                        "the network (for machines without a Mac or real Annex-B)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def _b64url_to_bytes(s: str) -> bytes:
    pad: str = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass
class ResolvedConfig:
    host: str
    port: int
    token: bytes
    name: str
    decoder: str
    display: Optional[str]
    fullscreen: bool
    input_enabled: bool


def _resolve_qr(qr: str) -> tuple[str, int, bytes, str]:
    """Parse --qr via protocol.parse_qr (lazy import)."""
    try:
        from telemachus_client.protocol import parse_qr  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "ERROR: --qr needs telemachus_client/protocol.py "
            "(owned by the protocol worker; not present yet)."
        ) from exc
    info: Any = parse_qr(qr)
    host: str = getattr(info, "host", None) or getattr(info, "address", None) or "127.0.0.1"
    port: int = int(getattr(info, "port", DEFAULT_PORT) or DEFAULT_PORT)
    token: Any = getattr(info, "token", b"")
    if isinstance(token, str):
        token = _b64url_to_bytes(token)
    name: str = getattr(info, "name", None) or getattr(info, "device_name", None) or DEFAULT_NAME
    return (host, port, bytes(token), str(name))


def _load_file_config() -> dict[str, Any]:
    """Return file-config values ({} when config.py is absent)."""
    try:
        from telemachus_client.config import load_config  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        cfg: Any = load_config()
    except Exception as exc:
        log.warning("config file unreadable (%s); using defaults", exc)
        return {}
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return cfg
    out: dict[str, Any] = {}
    for key in ("host", "port", "token", "name", "decoder", "display",
                "fullscreen", "input_enabled"):
        if hasattr(cfg, key):
            out[key] = getattr(cfg, key)
    return out


def resolve_config(args: argparse.Namespace) -> ResolvedConfig:
    """Apply precedence CLI > config file > defaults (via build_config)."""
    file_cfg: dict[str, Any] = _load_file_config()

    host: Optional[str] = args.host
    port: Optional[int] = args.port
    token: Optional[bytes] = _b64url_to_bytes(args.token_b64url) if args.token_b64url else None
    name: Optional[str] = args.name

    if args.qr:
        q_host, q_port, q_token, q_name = _resolve_qr(args.qr)
        host = host or q_host
        port = port if port is not None else q_port
        token = token if token is not None else q_token
        # Note: do NOT adopt the QR's name (q_name) as this client's device
        # name; the client's name is --name if given, else the local hostname.

    # Route through the frozen build_config(), which owns validation (port
    # range, token length, name truncation to <=64 UTF-8 bytes). It accepts a
    # Namespace or a Mapping; pass a mapping keyed with its exact field names
    # (argparse uses token_b64url/no_input, which build_config does not know).
    # None values mean "unset" and fall through to file_cfg/defaults.
    from telemachus_client.config import build_config  # noqa: PLC0415
    cli: dict[str, Any] = {
        "host": host,
        "port": port,
        "token": token,
        "name": name,
        "decoder": args.decoder,
        "display": args.display,
        "fullscreen": args.fullscreen,
        "input_enabled": False if args.no_input else None,
    }
    built = build_config(cli, file_cfg)
    return ResolvedConfig(
        host=built.host,
        port=built.port,
        token=built.token,
        name=built.name,
        decoder=built.decoder,
        display=built.display,
        fullscreen=built.fullscreen,
        input_enabled=built.input_enabled,
    )


def _persist_pairing(cfg: ResolvedConfig) -> None:
    """Save a ``--qr`` pairing so later runs need no URI.

    Only the connection facts are written (host/port/token/name); any decoder,
    display, or input setting already in the file is left alone. A write
    failure is logged, never fatal: the client already holds everything it
    needs in memory for this run.
    """
    try:
        from telemachus_client.config import save_config  # noqa: PLC0415
    except ImportError:
        return
    try:
        path = save_config(
            {
                "host": cfg.host,
                "port": cfg.port,
                "token": cfg.token,
                "name": cfg.name,
            }
        )
    except (OSError, ValueError) as exc:
        log.warning("could not save the pairing to the config file: %s", exc)
        return
    log.info("pairing saved to %s (later runs need no --qr)", path)


# -- Tailscale discovery ------------------------------------------------------
def _tailscale_auto_requested(args: argparse.Namespace) -> bool:
    """Offer discovery when no host is configured and stdin is interactive.

    Discovery is never automatic in scripts, cron, or systemd: without a TTY
    there is nobody to select a host, and connecting to the first peer would
    violate the explicit-selection rule.
    """
    if args.host is not None or args.qr or args.selftest or args.fake_payload:
        return False
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    return "host" not in _load_file_config()


def _print_peers(peers: list[Any], port: int) -> None:
    print(f"Telemachus hosts on Tailscale (port {port} open):")
    for index, peer in enumerate(peers, start=1):
        detail = ", ".join(part for part in (peer.os, peer.dns) if part)
        suffix = f" ({detail})" if detail else ""
        print(f"  {index}. {peer.name}  {peer.ip}{suffix}")


def _prompt_peer(peers: list[Any]) -> Optional[Any]:
    """Prompt for a numbered selection; blank input cancels."""
    while True:
        try:
            raw = input("Select a host by number (Enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return None
        try:
            index = int(raw)
        except ValueError:
            print(f"Enter a number between 1 and {len(peers)}.")
            continue
        if 1 <= index <= len(peers):
            return peers[index - 1]
        print(f"Enter a number between 1 and {len(peers)}.")


def _print_pairing_hint(host: str, port: int) -> None:
    """Explain how to pair without inventing or printing a token."""
    print(
        "No token configured for this host. On the Mac, switch to Wireless "
        "mode and show the pairing QR, then run:\n"
        f"  {PAIRING_EXECUTABLE} --host {host} --port {port} "
        "--qr '<telemachus:// pairing URI from the Mac>'"
    )


def apply_tailscale_discovery(
    args: argparse.Namespace, cfg: ResolvedConfig
) -> Optional[int]:
    """Offer a Tailscale host picker and update ``cfg``.

    Returns an exit code when the caller must stop (missing CLI, no
    candidates, cancelled selection, or no token), otherwise ``None`` so the
    normal connect path continues. ``--qr`` always wins: a paired URI already
    carries host, port, and token and is never overridden by discovery.
    """
    explicit: bool = bool(args.discover_tailscale)
    if args.qr or args.selftest or args.fake_payload:
        return None
    if not explicit and not _tailscale_auto_requested(args):
        return None

    from telemachus_client import tailscale  # noqa: PLC0415

    if not tailscale.tailscale_available():
        message = (
            "--discover-tailscale: the tailscale CLI is not on PATH. "
            "Install Tailscale or pass --host."
        )
        if explicit:
            log.error(message)
            print(message)
            return 1
        log.debug(message)
        return None

    peers: list[Any] = [p for p in tailscale.list_peers() if p.online]
    if not peers:
        message = "No online Tailscale peers were found."
        if explicit:
            print(message)
            return 1
        log.debug(message)
        return None

    hosts: list[Any] = tailscale.discover_hosts(peers, port=cfg.port)
    if not hosts:
        message = (
            f"Found {len(peers)} online Tailscale peer(s), but none accept "
            f"TCP on port {cfg.port}."
        )
        if explicit:
            print(message)
            return 1
        log.debug(message)
        return None

    _print_peers(hosts, cfg.port)
    selected: Optional[Any] = _prompt_peer(hosts)
    if selected is None:
        print("No host selected; not connecting.")
        return 1

    cfg.host = selected.ip
    log.info("tailscale: selected %s (%s)", selected.name, selected.ip)
    if not cfg.token:
        _print_pairing_hint(cfg.host, cfg.port)
        return 1
    return None


# -- synthetic exercise path -------------------------------------------------
def run_fake_payload(args: argparse.Namespace) -> int:
    """Exercise decode geometry + display with synthetic NV12 frames.

    No Mac, mock, or Annex-B needed. Renders a moving gradient when SDL2 is
    available; otherwise exercises the pure-math path and exits 0.
    """
    from telemachus_client.decode import (  # noqa: PLC0415
        build_ffmpeg_cmd, ffmpeg_available, find_render_nodes,
    )
    from telemachus_client.display import (  # noqa: PLC0415
        content_rect, match_display, normalize_to_content,
    )

    log.info("fake-payload: probing ffmpeg")
    log.info("ffmpeg_available=%s render_nodes=%s", ffmpeg_available(), find_render_nodes())
    for pref in ("auto", "vaapi", "cuda", "sw"):
        try:
            argv, backend = build_ffmpeg_cmd(1920, 1080, True, pref, None, "nv12", 0)  # type: ignore[arg-type]
            log.info("pref=%s backend=%s argv=%s", pref, backend, " ".join(argv))
        except FileNotFoundError:
            log.warning("ffmpeg missing; cannot build argv for pref=%s", pref)
    r = content_rect(1920, 1080, 1920, 1080)
    log.info("content_rect(1080p in 1080p)=%s", r)
    assert normalize_to_content(960, 540, 1920, 1080, 1920, 1080) == (0.5, 0.5)
    assert match_display(None, []) == 0

    try:
        from telemachus_client.display import SdlDisplay  # noqa: PLC0415
        import sdl2  # noqa: PLC0415, F401
    except ImportError:
        log.info("pysdl2/SDL2 absent; geometry + ffmpeg-probe exercise passed")
        return 0

    w, h = 640, 360
    disp = SdlDisplay(display_spec=args.display, fullscreen=False,
                      hud_enabled=True)
    disp.open(w, h)
    log.info("fake-payload: rendering synthetic NV12 (close window / Ctrl-C to stop)")
    import math

    frames: int = 0
    t0: float = time.monotonic()
    try:
        for i in range(600):
            y_plane = bytearray(w * h)
            stride_phase: int = (i * 4) % w
            for yy in range(h):
                base: int = (yy * 255 // h + i) % 256
                for xx in range(0, w, 16):
                    v: int = (base + ((xx + stride_phase) % w) * 255 // w) % 256
                    y_plane[yy * w + xx: yy * w + min(xx + 16, w)] = bytes([v] * min(16, w - xx))
            uv = bytearray(w * h // 2)
            u_val: int = int(128 + 100 * math.sin(i * 0.05))
            v_val: int = int(128 + 100 * math.cos(i * 0.05))
            for k in range(0, len(uv), 2):
                uv[k] = u_val
                uv[k + 1] = v_val
            disp.poll()
            if disp.should_quit:
                break
            disp.present(bytes(y_plane) + bytes(uv))
            frames += 1
            if i % 60 == 0:
                el: float = time.monotonic() - t0
                disp.set_stats(frames / max(el, 1e-6), 0.0, 0.0)
    finally:
        disp.close()
    el2: float = time.monotonic() - t0
    log.info("fake-payload: %d frames in %.1fs (%.1f fps)", frames, el2, frames / max(el2, 1e-6))
    return 0


# -- live client path ----------------------------------------------------------
class _StreamState:
    def __init__(self) -> None:
        self.lock: threading.Lock = threading.Lock()
        self.width: int = 0
        self.height: int = 0
        self.rotation: int = 0
        self.is_hevc: bool = True
        self.have_display: bool = False
        self.have_codec: bool = False
        self.fps: float = 0.0
        self.mbps: float = 0.0
        self.rtt_ms: float = 0.0
        self.connected: bool = False
        self.shutdown: bool = False
        self.frames_seen: int = 0
        self.generation: int = 0


def run_client(args: argparse.Namespace, cfg: ResolvedConfig) -> int:
    """Connect to the Mac host and run the decode/display loop."""
    try:
        from telemachus_client import decode as decode_mod  # noqa: PLC0415
        from telemachus_client.display import SdlDisplay, inverse_rotation  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(f"ERROR: {exc}. Is pysdl2 installed? (pip install pysdl2)") from exc
    try:
        from telemachus_client.netclient import StreamClient, CONNECT_TIMEOUT_S  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "ERROR: telemachus_client/netclient.py is missing "
            "(owned by the protocol worker; not present yet)."
        ) from exc

    if args.selftest:
        host, port = _start_selftest_server(cfg)
        cfg.host, cfg.port = host, port

    if not cfg.token:
        raise SystemExit("ERROR: no token. Pair with --qr 'telemachus://...' first.")

    state = _StreamState()
    stop = threading.Event()
    decoder_holder: dict[str, Any] = {"dec": None}
    client_holder: dict[str, Any] = {"client": None}

    keyframe_needed = threading.Event()

    def need_keyframe() -> None:
        keyframe_needed.set()

    def request_keyframe() -> None:
        client: Any = client_holder["client"]
        if client is None:
            return
        try:
            client.request_keyframe(force=True, reason="decoder-dead")
        except Exception:
            log.exception("request_keyframe failed")

    # The render thread alone creates, restarts and closes decoders. The
    # network callback only borrows the published decoder long enough to
    # enqueue a frame under state.lock; no pipe or SDL work happens there.
    decoder: Any = None
    decoder_generation = -1

    def ensure_decoder() -> Any:
        nonlocal decoder, decoder_generation
        with state.lock:
            w, h, rot, hevc = state.width, state.height, state.rotation, state.is_hevc
            generation, ready = state.generation, state.have_display
        if not ready or generation != decoder_generation:
            with state.lock:
                decoder_holder["dec"] = None
            if decoder is not None:
                decoder.close()
                decoder = None
        if not ready or w <= 0 or h <= 0:
            return None
        if decoder is None:
            decoder = decode_mod.FrameDecoder(
                w, h, hevc, decoder=cfg.decoder, rotation=rot,
                on_need_keyframe=need_keyframe,
            )
            decoder_generation = generation
        if decoder.proc is None or decoder.is_dead():
            with state.lock:
                decoder_holder["dec"] = None
            backend = decoder.start()
            log.info("decoder backend=%s %dx%d %s rot=%d",
                     backend, w, h, "hevc" if hevc else "h264", rot)
            need_keyframe()
        with state.lock:
            if generation == state.generation and not stop.is_set():
                decoder_holder["dec"] = decoder
        return decoder

    # -- protocol callbacks -------------------------------------------------
    def on_display(w: int, h: int, rotation: int) -> None:
        with state.lock:
            state.width, state.height, state.rotation = int(w), int(h), int(rotation or 0)
            state.have_display = True
            state.generation += 1
        log.info("display config %dx%d rot=%d", w, h, rotation or 0)

    def on_frame(payload: bytes, is_keyframe: bool, capture_ts: int) -> None:
        _ = capture_ts
        with state.lock:
            state.frames_seen += 1
            first: bool = state.frames_seen == 1
        if first:
            log.info("first frame: %d bytes keyframe=%s", len(payload), bool(is_keyframe))
        with state.lock:
            dec = decoder_holder["dec"]
            if dec is not None and decoder_generation == state.generation:
                dec.feed(payload, bool(is_keyframe))

    def on_codec(is_hevc: bool) -> None:
        with state.lock:
            state.is_hevc = bool(is_hevc)
            state.have_codec = True
            state.generation += 1
        log.info("codec selected: %s", "hevc" if is_hevc else "h264")

    def on_rtt(ms: float) -> None:
        with state.lock:
            state.rtt_ms = float(ms)

    def on_stats(fps: float, mbps: float) -> None:
        with state.lock:
            state.fps, state.mbps = float(fps), float(mbps)

    def on_shutdown() -> None:
        log.info("server shutdown; exiting")
        with state.lock:
            state.shutdown = True
        stop.set()

    def on_state(connected: bool) -> None:
        with state.lock:
            state.connected = bool(connected)
            if not connected:
                state.have_display = False
                state.have_codec = False
                state.generation += 1
                decoder_holder["dec"] = None
        log.info("connection %s", "up" if connected else "down")

    try:
        from telemachus_client.config import ClientConfig  # noqa: PLC0415
        client_cfg: Any = ClientConfig(
            host=cfg.host, port=cfg.port, token=cfg.token, name=cfg.name,
            decoder=cfg.decoder, display=cfg.display,
            fullscreen=cfg.fullscreen, input_enabled=cfg.input_enabled,
        )
    except ImportError:
        # config.py absent (protocol worker has not landed it yet): fall back
        # to a namespace object; StreamClient takes cfg with these attrs.
        from types import SimpleNamespace  # noqa: PLC0415
        client_cfg = SimpleNamespace(
            host=cfg.host, port=cfg.port, token=cfg.token, name=cfg.name,
            decoder=cfg.decoder, display=cfg.display,
            fullscreen=cfg.fullscreen, input_enabled=cfg.input_enabled,
        )

    disp = SdlDisplay(
        display_spec=cfg.display, fullscreen=cfg.fullscreen,
        hud_enabled=args.hud,
    )

    def on_touch(x: float, y: float, action: int,
                 count: int = 1, x2: float = 0.0, y2: float = 0.0) -> None:
        if not cfg.input_enabled:
            return
        if not disp.has_focus and action != 2:
            return
        client: Any = client_holder["client"]
        if client is None:
            return
        with state.lock:
            rotation = state.rotation
        x, y = inverse_rotation(x, y, rotation)
        if count == 2:
            x2, y2 = inverse_rotation(x2, y2, rotation)
        try:
            client.send_touch(float(x), float(y), int(action),
                              count=int(count), x2=float(x2), y2=float(y2))
        except Exception:
            log.exception("send_touch failed")

    disp.on_touch = on_touch if cfg.input_enabled else None

    client: Any = StreamClient(
        client_cfg,
        on_display=on_display, on_frame=on_frame, on_codec=on_codec,
        on_rtt=on_rtt, on_stats=on_stats, on_shutdown=on_shutdown,
        on_state=on_state,
    )
    client_holder["client"] = client

    # Fail fast on unreachable host with a clear message, using a lightweight
    # TCP connect-only probe (no handshake). The supervisor owns the real
    # handshake/token validation and reconnection logic.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(CONNECT_TIMEOUT_S)
            sock.connect((cfg.host, cfg.port))
    except OSError as exc:
        raise SystemExit(f"ERROR: cannot reach {cfg.host}:{cfg.port}: {exc}") from exc

    try:
        client.send_capabilities(supports_metadata=True, avc_only=False,
                                 device_info=None)
    except Exception:
        log.exception("send_capabilities failed (continuing)")

    net_thread = threading.Thread(
        target=_run_net, args=(client, stop), name="netclient", daemon=True
    )
    net_thread.start()

    # Wait for the first display config before opening the window so the
    # texture matches the stream.
    log.info("waiting for display config from %s:%d ...", cfg.host, cfg.port)
    deadline: float = time.monotonic() + 15.0
    while time.monotonic() < deadline and not stop.is_set():
        with state.lock:
            ready: bool = state.have_display
        if ready:
            break
        time.sleep(0.05)
    with state.lock:
        vw, vh = decode_mod.output_dims(state.width, state.height, state.rotation)
    if vw <= 0 or vh <= 0:
        log.error("no display config received; exiting")
        stop.set()
        client.close()
        net_thread.join(timeout=3.0)
        _stop_selftest_servers()
        return 1
    last_ping: float = 0.0
    last_open_dims: tuple[int, int] = (vw, vh)
    rc: int = 0
    try:
        try:
            disp.open(vw, vh)
        except ImportError as exc:
            raise SystemExit(
                f"ERROR: cannot open display ({exc}). pysdl2/SDL2 is missing "
                "(pip install pysdl2); protocol path already completed above."
            ) from exc
        except RuntimeError as exc:
            raise SystemExit(
                f"ERROR: cannot open display ({exc}); protocol path already "
                "completed above."
            ) from exc
        log.info("streaming: %s window (F1 toggles HUD). Ctrl-C / window close quits.",
                 "fullscreen" if cfg.fullscreen else "windowed")
        while not stop.is_set() and not disp.should_quit:
            with state.lock:
                shut: bool = state.shutdown
                fps, mbps, rtt = state.fps, state.mbps, state.rtt_ms
                cw, ch = decode_mod.output_dims(state.width, state.height, state.rotation)
            if shut:
                break
            if (cw, ch) != last_open_dims and cw > 0 and ch > 0:
                log.info("stream resized to %dx%d; reopening window", cw, ch)
                try:
                    disp.close()
                except Exception:
                    pass
                disp.open(cw, ch)
                last_open_dims = (cw, ch)
            disp.poll()
            if disp.should_quit:
                break
            dec_now = ensure_decoder()
            if keyframe_needed.is_set():
                keyframe_needed.clear()
                request_keyframe()
            if dec_now is not None:
                frame = dec_now.read_newest()
                # A new config can arrive during decoder startup. Present
                # only after the window has the matching output dimensions.
                if frame is not None and (dec_now.out_w, dec_now.out_h) == last_open_dims:
                    disp.present(frame)
            disp.set_stats(fps, mbps, rtt)
            now: float = time.monotonic()
            if now - last_ping >= 2.0:
                last_ping = now
                try:
                    client.send_ping()
                except Exception:
                    pass
            time.sleep(0.002)
    except KeyboardInterrupt:
        log.info("interrupted; exiting")
    finally:
        stop.set()
        try:
            disp.close()
        except Exception:
            pass
        with state.lock:
            decoder_holder["dec"] = None
        if decoder is not None:
            try:
                decoder.close()
            except Exception:
                log.exception("decoder cleanup failed")
        try:
            client.close()
        except Exception:
            pass
        net_thread.join(timeout=3.0)
        _stop_selftest_servers()
    return rc


def _run_net(client: Any, stop: threading.Event) -> None:
    try:
        run_sup: Optional[Callable[[], None]] = getattr(client, "run_supervisor", None)
        if callable(run_sup):
            run_sup()
        else:
            client.run()
    except Exception:
        if not stop.is_set():
            log.exception("network loop ended with error")
        stop.set()


# Selftest servers live for the whole run_client() call, including the
# SystemExit paths (token rejected, no display). They are stopped explicitly
# in run_client()'s finally block; the atexit hook covers any early exit.
_SELFTEST_SERVERS: list[Any] = []
_SELFTEST_ATEXIT: bool = False


def _stop_selftest_servers() -> None:
    servers, _SELFTEST_SERVERS[:] = list(_SELFTEST_SERVERS), []
    for srv in servers:
        try:
            srv.stop()
        except Exception:
            log.exception("selftest server stop failed")


def _start_selftest_server(cfg: ResolvedConfig) -> tuple[str, int]:
    """Start mockserver.MockTelemachusServer on loopback (lazy import).

    Binds on the calling thread — ``start()`` is non-blocking and returns
    the ephemeral port. Generates an ephemeral 32-byte token when the config
    carries none, and stores it back on ``cfg`` so the client handshake uses
    the same value. Returns the (host, port) the client should connect to.
    """
    global _SELFTEST_ATEXIT
    from telemachus_client.mockserver import MockTelemachusServer  # noqa: PLC0415

    token: bytes = cfg.token
    if not isinstance(token, bytes) or len(token) != 32:
        token = os.urandom(32)
        cfg.token = token
        log.info("selftest: no 32-byte token configured; generated an ephemeral one")
    server = MockTelemachusServer(token=token, fake_payload=False)
    port: int = server.start()
    _SELFTEST_SERVERS.append(server)
    if not _SELFTEST_ATEXIT:
        atexit.register(_stop_selftest_servers)
        _SELFTEST_ATEXIT = True
    log.info("selftest server at 127.0.0.1:%d", port)
    return ("127.0.0.1", port)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint; returns a process exit code."""
    parser: argparse.ArgumentParser = build_arg_parser()
    args: argparse.Namespace = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )
    if args.fake_payload:
        return run_fake_payload(args)
    if args.scout:
        from telemachus_client.scout import run_scout  # noqa: PLC0415
        return run_scout()
    try:
        cfg: ResolvedConfig = resolve_config(args)
    except ValueError as exc:
        raise SystemExit(f"ERROR: bad config: {exc}") from exc
    if args.qr:
        # Pairing is also persistence: the URI fills host/port/token/name, so
        # write them out now and later runs start with no arguments.
        _persist_pairing(cfg)
    discovery_rc: Optional[int] = apply_tailscale_discovery(args, cfg)
    if discovery_rc is not None:
        return discovery_rc
    log.info("connecting to %s:%d as %r (decoder=%s display=%s %s input=%s)",
             cfg.host, cfg.port, cfg.name, cfg.decoder,
             cfg.display or "1920x1080@60 default",
             "fullscreen" if cfg.fullscreen else "windowed",
             "on" if cfg.input_enabled else "off")
    return run_client(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
