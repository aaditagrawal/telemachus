"""Loopback test double implementing the real host side of the protocol.

Used for client integration tests without a Mac: it performs the genuine
handshake (including the bad-token path), collects capability bytes 8/9/12,
replies to the type-12 offer, sends ``codecSelected`` (only when the client
sent type 9) before ``displayConfig``, replays a synthetic Annex-B HEVC clip
as type-6 frames at a target rate, echoes pings as pongs, and counts
keyframe requests.

The generated clip is structurally valid Annex-B (4-byte start codes with
correct VPS/SPS/PPS/IDR NAL headers so sync-frame scans recognise IRAP
units) but is NOT decodable video. Pass ``fake_payload=True`` to send opaque
byte payloads instead (keyframe flags still cycle per ``keyframe_interval``).
"""

from __future__ import annotations

import hmac
import logging
import socket
import struct
import threading
import time
from typing import List, Optional, Tuple

from .protocol import (
    HANDSHAKE_PREFIX_LEN,
    MAGIC_REQUEST,
    MAGIC_RESPONSE,
    HandshakeStatus,
)

log = logging.getLogger(__name__)

_clip_lock = threading.Lock()


def _nal(nal_type: int, body: bytes) -> bytes:
    return b"\x00\x00\x00\x01" + bytes(((nal_type << 1) & 0xFE, 0x01)) + body


def generate_synthetic_hevc_clip(
    frames: int = 30,
    keyframe_interval: int = 10,
    idr_body_size: int = 512,
    p_body_size: int = 128,
) -> List[Tuple[bytes, bool]]:
    """Build ``[(payload, is_keyframe)]`` structural Annex-B HEVC units.

    Keyframes are VPS + SPS + PPS + IDR_W_RADL access units; inter frames
    are single TRAIL_R units. Bodies are deterministic filler, hence not
    decodable — sufficient for framing, sync-scan and throughput tests.
    """
    if frames <= 0:
        raise ValueError("frames must be positive")
    if keyframe_interval <= 0:
        raise ValueError("keyframe_interval must be positive")
    vps = _nal(32, b"VPS" + b"\x00" * 16)
    sps = _nal(33, b"SPS" + b"\x00" * 32)
    pps = _nal(34, b"PPS" + b"\x00" * 16)
    clip: List[Tuple[bytes, bool]] = []
    for i in range(frames):
        if i % keyframe_interval == 0:
            idr = _nal(19, b"IDR%04d" % i + b"\x00" * idr_body_size)
            clip.append((vps + sps + pps + idr, True))
        else:
            clip.append((_nal(1, b"P%04d" % i + b"\x00" * p_body_size), False))
    return clip


def _recvn(
    conn: socket.socket, count: int, stop: threading.Event
) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < count and not stop.is_set():
        try:
            chunk = conn.recv(count - len(buf))
        except socket.timeout:
            continue
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    if stop.is_set():
        return None
    return bytes(buf)


class MockTelemachusServer:
    def __init__(
        self,
        token: bytes,
        width: int = 1920,
        height: int = 1080,
        rotation: int = 0,
        fps: float = 30.0,
        keyframe_interval: int = 30,
        fake_payload: bool = False,
        handshake_timeout: float = 3.0,
        capability_timeout: float = 3.0,
    ) -> None:
        if len(token) != 32:
            raise ValueError(f"token must be 32 bytes, got {len(token)}")
        self._token = bytes(token)
        self._width = width
        self._height = height
        self._rotation = rotation
        self._fps = fps
        self._keyframe_interval = keyframe_interval
        self._fake_payload = fake_payload
        self._handshake_timeout = handshake_timeout
        self._capability_timeout = capability_timeout

        self.stats = {
            "keyframe_requests": 0,
            "keyframe_forced": 0,
            "pings": 0,
            "connected_events": 0,
            "touches": 0,
            "device_infos": [],
        }
        self._stats_lock = threading.Lock()

        self._stop = threading.Event()
        self._listen_sock: Optional[socket.socket] = None
        self._conn: Optional[socket.socket] = None
        self._conn_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._accept_thread: Optional[threading.Thread] = None
        self._handler_threads: List[threading.Thread] = []
        self.port = 0

    # -- lifecycle ------------------------------------------------------

    def start(self) -> int:
        """Bind loopback on an ephemeral port, serve in background; returns it."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        sock.settimeout(0.5)
        self._listen_sock = sock
        self.port = sock.getsockname()[1]
        self._stop.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="mock-accept", daemon=True
        )
        self._accept_thread.start()
        return self.port

    def stop(self) -> None:
        self._stop.set()
        if self._listen_sock is not None:
            try:
                self._listen_sock.close()
            except OSError:
                pass
            self._listen_sock = None
        with self._conn_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=5.0)
            self._accept_thread = None
        for thread in self._handler_threads:
            thread.join(timeout=5.0)
        self._handler_threads = []

    def __enter__(self) -> "MockTelemachusServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            value = self.stats[key]
            self.stats[key] = value + amount if isinstance(value, int) else value

    # -- accept ----------------------------------------------------------

    def _accept_loop(self) -> None:
        assert self._listen_sock is not None
        while not self._stop.is_set():
            try:
                conn, _addr = self._listen_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._conn_lock:
                old, self._conn = self._conn, conn
            if old is not None:
                # Mirror the host: a new admission cancels the old session.
                try:
                    old.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    old.close()
                except OSError:
                    pass
            handler = threading.Thread(
                target=self._handle, args=(conn,), name="mock-conn", daemon=True
            )
            self._handler_threads.append(handler)
            handler.start()

    # -- per-connection --------------------------------------------------

    def _send(self, conn: socket.socket, data: bytes) -> bool:
        with self._send_lock:
            try:
                conn.sendall(data)
                return True
            except OSError:
                return False

    def _handle(self, conn: socket.socket) -> None:
        try:
            if not self._do_handshake(conn):
                return
            caps = self._collect_capabilities(conn)
            if caps is None:
                return
            meta, avc_only = caps
            if avc_only:
                # Sent ONLY to clients that opted in via type 9, and before
                # the display config (StreamingServer.swift:340-355).
                self._send(conn, bytes((10, 1)))
            self._send(
                conn,
                bytes((1,))
                + struct.pack(">i", self._width)
                + struct.pack(">i", self._height)
                + struct.pack(">i", self._rotation),
            )
            with self._stats_lock:
                self.stats["connected_events"] += 1
            sender_stop = threading.Event()
            sender = threading.Thread(
                target=self._stream_frames,
                args=(conn, sender_stop, meta),
                name="mock-stream",
                daemon=True,
            )
            sender.start()
            try:
                self._read_loop(conn)
            finally:
                sender_stop.set()
                sender.join(timeout=5.0)
        except OSError:
            # stop() or a newer connection can close this socket while the
            # handler is setting its timeout or reading a handshake.
            log.debug("mock session closed", exc_info=True)
        finally:
            with self._conn_lock:
                if self._conn is conn:
                    self._conn = None
            try:
                conn.close()
            except OSError:
                pass

    def _do_handshake(self, conn: socket.socket) -> bool:
        conn.settimeout(0.5)
        deadline = time.monotonic() + self._handshake_timeout
        prefix = bytearray()
        while len(prefix) < HANDSHAKE_PREFIX_LEN and not self._stop.is_set():
            if time.monotonic() > deadline:
                try:
                    conn.close()
                except OSError:
                    pass
                return False
            try:
                chunk = conn.recv(HANDSHAKE_PREFIX_LEN - len(prefix))
            except socket.timeout:
                continue
            except OSError:
                return False
            if not chunk:
                return False
            prefix += chunk
        if self._stop.is_set():
            return False
        if bytes(prefix[0:4]) != MAGIC_REQUEST:
            self._send(conn, MAGIC_RESPONSE + bytes((HandshakeStatus.INVALID_MAGIC,)))
            return False
        name_len = prefix[36]
        if not 1 <= name_len <= 64:
            self._send(conn, MAGIC_RESPONSE + bytes((HandshakeStatus.INVALID_NAME,)))
            return False
        name_raw = _recvn(conn, name_len, self._stop)
        if name_raw is None:
            return False
        try:
            name = name_raw.decode("utf-8")
        except UnicodeDecodeError:
            name = ""
        if not name:
            self._send(conn, MAGIC_RESPONSE + bytes((HandshakeStatus.INVALID_NAME,)))
            return False
        if not hmac.compare_digest(bytes(prefix[4:36]), self._token):
            self._send(conn, MAGIC_RESPONSE + bytes((HandshakeStatus.INVALID_TOKEN,)))
            return False
        self._send(conn, MAGIC_RESPONSE + bytes((HandshakeStatus.OK,)))
        return True

    def _collect_capabilities(
        self, conn: socket.socket
    ) -> Optional[Tuple[bool, bool]]:
        """Wait for capability bytes 8 (and 12); returns (meta, avc_only)."""
        buf = bytearray()
        meta = False
        avc_only = False
        got_8 = False
        got_12 = False
        deadline = time.monotonic() + self._capability_timeout
        conn.settimeout(0.2)
        while time.monotonic() < deadline and not self._stop.is_set():
            if got_8 and got_12:
                break
            try:
                chunk = conn.recv(256)
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            buf += chunk
            while buf:
                msg = buf[0]
                if msg == 9 and len(buf) >= 1:
                    avc_only = True
                    del buf[0:1]
                elif msg == 8 and len(buf) >= 1:
                    meta = True
                    got_8 = True
                    del buf[0:1]
                elif msg == 12 and len(buf) >= 1:
                    got_12 = True
                    del buf[0:1]
                    # Accept the offer so the client sends the 66-byte type 11.
                    self._send(conn, bytes((12,)))
                    deadline = min(deadline, time.monotonic()) + 2.0
                elif msg == 11 and len(buf) >= 66:
                    model_raw = bytes(buf[1:65])
                    hz = buf[65]
                    model = model_raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
                    with self._stats_lock:
                        self.stats["device_infos"].append((model, hz))
                    del buf[0:66]
                elif msg == 4 and len(buf) >= 9:
                    self._answer_ping(conn, bytes(buf[1:9]))
                    del buf[0:9]
                elif msg == 7 and len(buf) >= 2:
                    self._count_keyframe(buf[1])
                    del buf[0:2]
                elif msg == 2 and len(buf) >= 2 and buf[1] in (1, 2):
                    need = 2 + buf[1] * 8 + 4
                    if len(buf) < need:
                        break
                    del buf[0:need]
                    self._bump("touches")
                elif msg in (2, 4, 7, 8, 9, 11, 12):
                    break  # need more bytes
                else:
                    del buf[0:1]  # mirror host: skip unknown input bytes
        return (meta, avc_only)

    def _answer_ping(self, conn: socket.socket, stamp: bytes) -> None:
        self._bump("pings")
        self._send(conn, bytes((5,)) + stamp)

    def _count_keyframe(self, flags: int) -> None:
        self._bump("keyframe_requests")
        if flags & 1:
            self._bump("keyframe_forced")

    def _read_loop(self, conn: socket.socket) -> None:
        buf = bytearray()
        conn.settimeout(0.5)
        while not self._stop.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while buf:
                msg = buf[0]
                if msg == 4 and len(buf) >= 9:
                    self._answer_ping(conn, bytes(buf[1:9]))
                    del buf[0:9]
                elif msg == 7 and len(buf) >= 2:
                    self._count_keyframe(buf[1])
                    del buf[0:2]
                elif msg == 2 and len(buf) >= 2 and buf[1] in (1, 2):
                    need = 2 + buf[1] * 8 + 4
                    if len(buf) < need:
                        break
                    del buf[0:need]
                    self._bump("touches")
                elif msg == 12 and len(buf) >= 1:
                    del buf[0:1]
                    self._send(conn, bytes((12,)))
                elif msg == 11 and len(buf) >= 66:
                    model_raw = bytes(buf[1:65])
                    hz = buf[65]
                    model = model_raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
                    with self._stats_lock:
                        self.stats["device_infos"].append((model, hz))
                    del buf[0:66]
                elif msg in (1, 8, 9) and len(buf) >= 1:
                    del buf[0:1]
                elif msg in (2, 4, 7, 11):
                    break  # truncated; wait for more
                else:
                    del buf[0:1]

    def _stream_frames(
        self, conn: socket.socket, stop: threading.Event, meta: bool
    ) -> None:
        if self._fake_payload:
            clip: List[Tuple[bytes, bool]] = [
                (b"FAKEFRAME-%08d" % i + b"." * 64, i % self._keyframe_interval == 0)
                for i in range(max(self._keyframe_interval, 1))
            ]
        else:
            clip = generate_synthetic_hevc_clip(
                frames=max(self._keyframe_interval * 2, 2),
                keyframe_interval=self._keyframe_interval,
            )
        period = 1.0 / self._fps if self._fps > 0 else 1.0 / 30.0
        index = 0
        while not stop.is_set() and not self._stop.is_set():
            payload, is_key = clip[index % len(clip)]
            index += 1
            if meta:
                packet = (
                    bytes((6,))
                    + struct.pack(">i", len(payload))
                    + bytes((1 if is_key else 0,))
                    + struct.pack(">Q", time.monotonic_ns())
                    + payload
                )
            else:
                packet = bytes((0,)) + struct.pack(">i", len(payload)) + payload
            if not self._send(conn, packet):
                return
            stop.wait(period)
