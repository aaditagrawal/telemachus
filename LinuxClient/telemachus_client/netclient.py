"""Blocking-socket TCP client for the Telemachus stream (stdlib only).

Threading model: :meth:`StreamClient.run` and :meth:`run_supervisor` block
the calling thread; :meth:`close` is safe to call from any other thread (or
from inside a callback) and unblocks a pending ``recv`` via ``shutdown``.
All socket writes are serialized by a send lock. Callbacks are invoked on
the network thread and must return quickly; exceptions they raise are
logged and swallowed so one bad observer cannot kill the stream.
"""

from __future__ import annotations

import logging
import random
import socket
import threading
import time
from typing import Callable, Optional, Tuple

from .config import ClientConfig
from .protocol import (
    CodecSelectedEvent,
    DeviceInfoAcceptedEvent,
    DisplayConfigEvent,
    FrameEvent,
    FrameParser,
    HandshakeStatus,
    PongEvent,
    ProtocolError,
    ServerShutdownEvent,
    TokenRejectedError,
    encode_capability,
    encode_device_info,
    encode_handshake,
    encode_keyframe_request,
    encode_ping,
    encode_touch,
    parse_handshake_response,
)

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 5.0
RECV_TIMEOUT_S = 1.0
RECV_BUF_SIZE = 65536

#: Reconnect delays in seconds: 1/2/5/10 s plus up-to-1 s jitter.
BACKOFF_SCHEDULE = (1.0, 2.0, 5.0, 10.0)
#: A session that dies faster than this suggests a second client took the
#: session (the host cancels the old connection on new admission), so back
#: off hard instead of fighting it.
QUICK_DROP_NS = 3_000_000_000
#: A session living this long resets the backoff progression.
HEALTHY_SESSION_NS = 30_000_000_000

OnDisplay = Callable[[int, int, int], None]
OnFrame = Callable[[bytes, bool, int], None]
OnCodec = Callable[[bool], None]
OnRtt = Callable[[float], None]
OnStats = Callable[[float, float], None]
OnShutdown = Callable[[], None]
OnState = Callable[[bool], None]


def _recvn(sock: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        try:
            chunk = sock.recv(count - len(buf))
        except socket.timeout:
            raise ProtocolError("timed out waiting for handshake response") from None
        if not chunk:
            raise ProtocolError("connection closed during handshake")
        buf += chunk
    return bytes(buf)


class StreamClient:
    def __init__(
        self,
        cfg: ClientConfig,
        on_display: Optional[OnDisplay] = None,
        on_frame: Optional[OnFrame] = None,
        on_codec: Optional[OnCodec] = None,
        on_rtt: Optional[OnRtt] = None,
        on_stats: Optional[OnStats] = None,
        on_shutdown: Optional[OnShutdown] = None,
        on_state: Optional[OnState] = None,
    ) -> None:
        self._cfg = cfg
        self._on_display = on_display
        self._on_frame = on_frame
        self._on_codec = on_codec
        self._on_rtt = on_rtt
        self._on_stats = on_stats
        self._on_shutdown = on_shutdown
        self._on_state = on_state

        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._kf_lock = threading.Lock()
        self._connected = False
        self._closed = False
        self._stop = threading.Event()
        self._graceful_shutdown = False

        self._caps: Tuple[bool, bool, Optional[Tuple[str, int]]] = (True, False, None)
        self._pending_device_info: Optional[bytes] = None
        self._device_info_sent = False

        self._last_kf_request_ns = 0
        self._last_keyframe_mono_ns: Optional[int] = None
        self._ping_since_start = False

        self._stat_bytes = 0
        self._stat_frames = 0
        self._stat_start = time.monotonic()

        self.stream_is_hevc = True
        self.codec_negotiated = False

    @property
    def connected(self) -> bool:
        return self._connected

    # -- connection -----------------------------------------------------

    def connect(self) -> None:
        """Open TCP, set TCP_NODELAY, and run the wireless handshake.

        The handshake is skipped when ``cfg.token`` is empty (USB/loopback
        mode, where the host admits loopback clients without authentication).
        Raises :class:`TokenRejectedError` on status 1 and
        :class:`ProtocolError` on statuses 2/3 or any transport failure.
        """
        with self._state_lock:
            if self._closed or self._stop.is_set():
                raise ProtocolError("client is closed")
            if self._sock is not None:
                raise ProtocolError("already connected or connecting")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Publish the connecting socket so close() can interrupt both
            # TCP connect and the handshake, before _connected becomes true.
            self._sock = sock
            self._graceful_shutdown = False
        try:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                log.debug("TCP_NODELAY unavailable", exc_info=True)
            sock.settimeout(CONNECT_TIMEOUT_S)
            try:
                sock.connect((self._cfg.host, self._cfg.port))
            except OSError as exc:
                raise ProtocolError(
                    f"cannot reach {self._cfg.host}:{self._cfg.port}: {exc}"
                ) from exc

            if self._cfg.token:
                if len(self._cfg.token) != 32:
                    raise ProtocolError(
                        f"token must be 32 bytes, got {len(self._cfg.token)}"
                    )
                try:
                    sock.sendall(encode_handshake(self._cfg.token, self._cfg.name))
                    raw = _recvn(sock, 5)
                except OSError as exc:
                    raise ProtocolError(f"handshake I/O failed: {exc}") from exc
                status = parse_handshake_response(raw)
                if status is HandshakeStatus.INVALID_TOKEN:
                    raise TokenRejectedError("host rejected the bearer token")
                if status is not HandshakeStatus.OK:
                    raise ProtocolError(f"handshake failed: {status.name}")

            sock.settimeout(RECV_TIMEOUT_S)
            with self._state_lock:
                if self._closed or self._sock is not sock:
                    raise ProtocolError("client closed during connect")
                self._connected = True
                self._device_info_sent = False
                self._last_keyframe_mono_ns = None
                self._ping_since_start = False
                self._stat_bytes = 0
                self._stat_frames = 0
                self._stat_start = time.monotonic()
        except Exception:
            with self._state_lock:
                if self._sock is sock:
                    self._sock = None
            sock.close()
            raise
        self._fire(self._on_state, True)

    def send_capabilities(
        self,
        supports_metadata: bool,
        avc_only: bool,
        device_info: Optional[Tuple[str, int]] = None,
    ) -> None:
        """Advertise capabilities in the mandated order: 9 before 8, 12 last.

        ``device_info`` (model, max_hz) is held back and sent as type 11 only
        after the host accepts with its own type-12 byte. The arguments are
        remembered so :meth:`run_supervisor` can re-advertise on reconnect.
        """
        self._caps = (supports_metadata, avc_only, device_info)
        self._pending_device_info = None
        if device_info is not None:
            model, max_hz = device_info
            # Raises ValueError immediately on bad input instead of failing
            # silently later inside the network thread.
            self._pending_device_info = encode_device_info(model, max_hz)
        out = bytearray()
        if avc_only:
            out += encode_capability(9)
        if supports_metadata:
            out += encode_capability(8)
        out += encode_capability(12)
        self._send(bytes(out))

    # -- outbound -------------------------------------------------------

    def request_keyframe(self, force: bool = False, reason: str = "") -> None:
        """Ask the host for an IDR. Non-forced requests are throttled to 1
        per 500 ms and share one guard across all callers (StreamClient.kt);
        forced requests bypass the throttle. Silent no-op when disconnected,
        mirroring the Android client."""
        now = time.monotonic_ns()
        with self._kf_lock:
            if (
                not force
                and self._last_kf_request_ns > 0
                and now - self._last_kf_request_ns
                < 500_000_000
            ):
                return
            self._last_kf_request_ns = now
        if reason:
            log.debug("requesting keyframe: reason=%s force=%s", reason, force)
        self._send(encode_keyframe_request(force))

    def send_touch(
        self,
        x: float,
        y: float,
        action: int,
        count: int = 1,
        x2: float = 0.0,
        y2: float = 0.0,
    ) -> None:
        """Send a touch event (0 = down, 1 = move, 2 = up). Dropped when
        ``cfg.input_enabled`` is false or while disconnected."""
        if not self._cfg.input_enabled:
            return
        try:
            self._send(encode_touch(x, y, action, count, x2, y2))
        except ValueError:
            log.warning("dropping invalid touch event", exc_info=True)

    def send_ping(self) -> None:
        """Send a ping stamped with the client monotonic clock."""
        self._ping_since_start = True
        self._send(encode_ping(time.monotonic_ns()))

    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None or not self._connected:
            return
        with self._send_lock:
            try:
                sock.sendall(data)
            except OSError:
                log.debug("send failed; run loop will observe EOF", exc_info=True)

    # -- dispatch loop --------------------------------------------------

    def run(self) -> None:
        """Read and dispatch host messages until shutdown, error or close().

        A type-3 arrival sets the graceful flag so :meth:`run_supervisor`
        will not reconnect. Unknown types / bad framing end the loop with a
        clean disconnect, mirroring StreamClient.kt:420-427.
        """
        sock = self._sock
        if sock is None or not self._connected:
            raise ProtocolError("not connected; call connect() first")
        parser = FrameParser()
        try:
            while not self._closed:
                try:
                    chunk = sock.recv(RECV_BUF_SIZE)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                try:
                    events = list(parser.feed(chunk))
                except ProtocolError:
                    log.warning("protocol violation; disconnecting", exc_info=True)
                    break
                for event in events:
                    if self._closed:
                        break
                    self._dispatch(event)
        finally:
            self._teardown()

    def _dispatch(self, event: object) -> None:
        if isinstance(event, DisplayConfigEvent):
            self._fire(self._on_display, event.width, event.height, event.rotation)
        elif isinstance(event, FrameEvent):
            self._on_frame_event(event)
        elif isinstance(event, PongEvent):
            if self._ping_since_start:
                rtt_ms = (time.monotonic_ns() - event.sent_ns) / 1_000_000.0
                self._fire(self._on_rtt, max(0.0, rtt_ms))
        elif isinstance(event, CodecSelectedEvent):
            self.stream_is_hevc = event.is_hevc
            self.codec_negotiated = True
            self._fire(self._on_codec, event.is_hevc)
        elif isinstance(event, ServerShutdownEvent):
            log.debug("server shut down gracefully; will not reconnect")
            self._graceful_shutdown = True
            self._fire(self._on_shutdown)
            self._closed = True
        elif isinstance(event, DeviceInfoAcceptedEvent):
            if self._pending_device_info is not None and not self._device_info_sent:
                self._device_info_sent = True
                self._send(self._pending_device_info)
        else:  # pragma: no cover - exhaustive today
            log.warning("unhandled event %r", event)

    def _on_frame_event(self, event: FrameEvent) -> None:
        now_ns = time.monotonic_ns()
        if event.is_keyframe:
            self._last_keyframe_mono_ns = now_ns
        elif (
            self._last_keyframe_mono_ns is not None
            and now_ns - self._last_keyframe_mono_ns > 1_500_000_000
        ):
            age_ms = (now_ns - self._last_keyframe_mono_ns) // 1_000_000
            self.request_keyframe(
                force=False, reason=f"last keyframe {age_ms}ms ago"
            )
        self._stat_bytes += len(event.payload)
        self._stat_frames += 1
        elapsed = time.monotonic() - self._stat_start
        if elapsed >= 1.0:
            fps = self._stat_frames / elapsed
            mbps = (self._stat_bytes * 8.0) / elapsed / 1_000_000.0
            self._stat_bytes = 0
            self._stat_frames = 0
            self._stat_start = time.monotonic()
            self._fire(self._on_stats, fps, mbps)
        self._fire(
            self._on_frame, event.payload, event.is_keyframe, event.capture_timestamp
        )

    def _fire(self, callback: Optional[Callable], *args: object) -> None:
        if callback is None:
            return
        try:
            callback(*args)  # type: ignore[operator]
        except Exception:
            log.exception("client callback %r raised", callback)

    def _teardown(self) -> None:
        with self._state_lock:
            was_connected = self._connected
            self._connected = False
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if was_connected:
            self._fire(self._on_state, False)

    def close(self) -> None:
        """Permanently stop this client, including a pending handshake.

        Idempotent and safe from any thread. A close before the supervisor
        starts must also prevent it from creating a new connection.
        """
        with self._state_lock:
            self._closed = True
            self._stop.set()
        self._teardown()

    # -- supervision ----------------------------------------------------

    def run_supervisor(self) -> None:
        """Connect, run, and reconnect with 1/2/5/10 s backoff plus jitter.

        Never reconnects after a graceful :class:`ServerShutdownEvent`.
        :class:`TokenRejectedError` is re-raised after cleanup so the caller
        can prompt re-pairing. A session that dies within 3 s is treated as
        fighting a second client and backs off the full 10 s; a session
        living 30 s or more resets the progression.
        """
        backoff_idx = 0
        while not self._stop.is_set():
            try:
                self.connect()
            except TokenRejectedError:
                self._fire(self._on_state, False)
                raise
            except (OSError, ProtocolError) as exc:
                log.info("connect failed (%s); backing off", exc)
                self._fire(self._on_state, False)
                backoff_idx = self._sleep_backoff(backoff_idx)
                continue
            supports_metadata, avc_only, device_info = self._caps
            self.send_capabilities(supports_metadata, avc_only, device_info)
            started_ns = time.monotonic_ns()
            self.run()
            if self._graceful_shutdown or self._stop.is_set():
                return
            lived_ns = time.monotonic_ns() - started_ns
            if lived_ns >= HEALTHY_SESSION_NS:
                backoff_idx = 0
                delay = BACKOFF_SCHEDULE[0]
            elif lived_ns < QUICK_DROP_NS:
                log.info(
                    "session died after %.2fs; long backoff to avoid "
                    "fighting a second client",
                    lived_ns / 1_000_000_000.0,
                )
                delay = BACKOFF_SCHEDULE[-1]
            else:
                delay = BACKOFF_SCHEDULE[min(backoff_idx, len(BACKOFF_SCHEDULE) - 1)]
                backoff_idx += 1
            if self._stop.wait(delay + random.uniform(0, 1.0)):
                return

    def _sleep_backoff(self, backoff_idx: int) -> int:
        delay = BACKOFF_SCHEDULE[min(backoff_idx, len(BACKOFF_SCHEDULE) - 1)]
        self._stop.wait(delay + random.uniform(0, 1.0))
        return backoff_idx + 1
