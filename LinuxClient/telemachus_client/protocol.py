"""Telemachus wire-protocol codec for the Linux client.

Every layout below was verified against the real implementations before
writing (citations are file:line in the Telemachus repo):

* Handshake request/response layout + fixed 37-byte prefix:
  ``MacHost/Sources/HandshakeCodec.swift:22-24``,
  ``AndroidClient/.../display/AuthHandshake.kt:25-33``.
* Message type numbers: ``MacHost/Sources/StreamingServer.swift:4-31``.
* Display/frame headers big-endian: ``StreamingServer.swift:517-521``
  (``Int32.bigEndian``) and ``:810-813``; read with Java ``readInt``
  (big-endian) in ``StreamClient.kt:385-387`` / ``:556``.
* Frame-with-metadata timestamp big-endian: ``StreamingServer.swift:795-796``
  (``timestamp.bigEndian``); read with Java ``readLong`` (big-endian) in
  ``StreamClient.kt:565``.
* Touch/ping little-endian: ``StreamClient.kt:453`` / ``:523``
  (``ByteOrder.LITTLE_ENDIAN``); the host reads them with native
  ``loadUnaligned`` (``StreamingServer.swift:671-682``), which is
  little-endian on every supported Mac/iPhone CPU. Pong is a raw byte echo
  (``StreamingServer.swift:607-610``), parsed as LE i64
  (``StreamClient.kt:395``).
* Capability ordering (9 before 8, 12 after; type 8 triggers
  ``finishProtocolStartup``): ``StreamClient.kt:135-137``,
  ``StreamingServer.swift:621-642``. ``codecSelected`` precedes
  ``displayConfig``: ``StreamingServer.swift:340-355``.
* Device-info 66-byte shape + 63-byte truncation keeps the last byte zero:
  ``StreamClient.kt:317-325``; host parse: ``StreamingServer.swift:653-661``.
* Unknown inbound type disconnects: ``StreamClient.kt:420-427``.
* Keyframe throttle/stale constants: ``StreamClient.kt:668-669`` and
  freshness policy ``:599-617``.
* Handshake/client timeouts 5 s: ``StreamClient.kt:670-671``.
* Codec wire ids (0 = HEVC, 1 = H.264):
  ``MacHost/Sources/CodecLimits.swift:7-12`` + ``StreamClient.kt:402``.

Known doc discrepancy: ``docs/protocol.md:42-61`` omits message types 3, 11
and 12 and says only that "video and display payload fields use network byte
order" without documenting that client-to-host touch/ping fields are
little-endian. This module implements what the code does.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, Union
from urllib.parse import urlparse, unquote

MAGIC_REQUEST = b"SSWA"
MAGIC_RESPONSE = b"SSWR"

#: 4 (magic) + 32 (token) + 1 (name length); see HandshakeCodec.fixedPrefixLen.
HANDSHAKE_PREFIX_LEN = 37
#: 4 (magic) + 1 (status).
HANDSHAKE_RESPONSE_LEN = 5
#: Client-side handshake timeout, mirroring HANDSHAKE_TIMEOUT_MS.
HANDSHAKE_TIMEOUT_S = 5.0
#: Default TCP port used by the Mac listener, USB scripts and Android client.
DEFAULT_PORT = 54321

MAX_FRAME_SIZE = 5 * 1024 * 1024

#: Flag bit in the type-6 frame header marking a sync (keyframe) frame.
FRAME_FLAG_KEYFRAME = 1
#: Flag bit in the type-7 keyframe request forcing an IDR outside throttling.
KEYFRAME_REQUEST_FLAG_FORCE = 1

#: Mirror of StreamClient KEYFRAME_STALE_INTERVAL_NS: staleness threshold.
KEYFRAME_STALE_NS = 1_500_000_000
#: Mirror of StreamClient KEYFRAME_REQUEST_INTERVAL_NS: non-forced throttle.
KEYFRAME_REQUEST_INTERVAL_NS = 500_000_000


class HandshakeStatus(IntEnum):
    OK = 0
    INVALID_TOKEN = 1
    INVALID_MAGIC = 2
    INVALID_NAME = 3


class MessageType(IntEnum):
    LEGACY_VIDEO_FRAME = 0
    DISPLAY_CONFIG = 1
    TOUCH_EVENT = 2
    SERVER_SHUTDOWN = 3
    PING = 4
    PONG = 5
    VIDEO_FRAME_WITH_METADATA = 6
    KEYFRAME_REQUEST = 7
    CLIENT_SUPPORTS_FRAME_METADATA = 8
    CLIENT_AVC_ONLY = 9
    CODEC_SELECTED = 10
    CLIENT_DEVICE_INFO = 11
    DEVICE_INFO_CAPABILITY = 12


class ProtocolError(Exception):
    """Raised for any wire-protocol violation (bad magic, length, framing)."""


class TokenRejectedError(Exception):
    """Raised when the host rejects the wireless bearer token (status 1)."""


@dataclass(frozen=True)
class QRInfo:
    host: str
    port: int
    token: bytes
    name: str


@dataclass(frozen=True)
class DisplayConfigEvent:
    width: int
    height: int
    rotation: int


@dataclass(frozen=True)
class FrameEvent:
    payload: bytes
    is_keyframe: bool
    #: Host uptime nanoseconds for type 6; 0 for legacy type 0 (no timestamp
    #: on the wire). Never comparable to the client clock.
    capture_timestamp: int


@dataclass(frozen=True)
class PongEvent:
    sent_ns: int


@dataclass(frozen=True)
class CodecSelectedEvent:
    is_hevc: bool


@dataclass(frozen=True)
class ServerShutdownEvent:
    pass


@dataclass(frozen=True)
class DeviceInfoAcceptedEvent:
    """Host accepted the type-12 offer; the client may now send type 11."""


#: Any single message the host can send, as yielded by FrameParser.feed.
Event = Union[
    DisplayConfigEvent,
    FrameEvent,
    PongEvent,
    CodecSelectedEvent,
    ServerShutdownEvent,
    DeviceInfoAcceptedEvent,
]


def encode_handshake(token: bytes, name: str) -> bytes:
    """Build ``SSWA | token[32] | len(name) | name`` (AuthHandshake.kt:25-33)."""
    if len(token) != 32:
        raise ValueError(f"token must be 32 bytes, got {len(token)}")
    name_bytes = name.encode("utf-8")
    if not 1 <= len(name_bytes) <= 64:
        raise ValueError(
            f"device name UTF-8 length must be 1..64, got {len(name_bytes)}"
        )
    return MAGIC_REQUEST + bytes(token) + struct.pack("B", len(name_bytes)) + name_bytes


def parse_handshake_response(buf: bytes) -> HandshakeStatus:
    """Parse the 5-byte ``SSWR | status`` response (AuthHandshake.kt:38-42)."""
    if len(buf) != HANDSHAKE_RESPONSE_LEN:
        raise ProtocolError(
            f"handshake response must be {HANDSHAKE_RESPONSE_LEN} bytes, "
            f"got {len(buf)}"
        )
    if bytes(buf[:4]) != MAGIC_RESPONSE:
        raise ProtocolError(f"bad handshake magic: {bytes(buf[:4])!r}")
    try:
        return HandshakeStatus(buf[4])
    except ValueError:
        raise ProtocolError(f"unknown handshake status: {buf[4]}") from None


_TOKEN_B64URL_RE = re.compile(r"^[A-Za-z0-9\-_]+$")


def _decode_token_b64url(text: str) -> bytes:
    try:
        raw_text = text.encode("ascii")
    except UnicodeEncodeError:
        raise ProtocolError("token is not ASCII base64url") from None
    if not _TOKEN_B64URL_RE.match(text):
        raise ProtocolError("token is not base64url")
    padded = raw_text + b"=" * (-len(raw_text) % 4)
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProtocolError(f"malformed token: {exc}") from None
    if len(raw) != 32:
        raise ProtocolError(f"token must decode to 32 bytes, got {len(raw)}")
    return raw


def parse_qr(uri: str) -> QRInfo:
    """Parse ``telemachus://HOST:PORT?t=BASE64URL&name=URLENC``.

    Mirrors ``PairingURL.parse``: missing ``name`` defaults to ``"Mac"``.
    Raises :class:`ProtocolError` on any malformed input.
    """
    parts = urlparse(uri)
    if parts.scheme != "telemachus":
        raise ProtocolError(f"bad QR scheme: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise ProtocolError("QR URI is missing a host")
    try:
        port = parts.port
    except ValueError:
        raise ProtocolError("QR URI has an invalid port") from None
    if port is None or not 1 <= port <= 65535:
        raise ProtocolError(f"QR URI has an invalid port: {port}")
    token_b64: Union[str, None] = None
    name = "Mac"
    for item in parts.query.split("&"):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key == "t" and token_b64 is None:
            token_b64 = value
        elif key == "name":
            # unquote (not unquote_plus): '+' is a literal plus, matching
            # Android Uri.getQueryParameter decoding.
            name = unquote(value)
    if token_b64 is None:
        raise ProtocolError("QR URI is missing the token (t=)")
    return QRInfo(host=host, port=port, token=_decode_token_b64url(token_b64), name=name)


def encode_touch(
    x: float,
    y: float,
    action: int,
    count: int = 1,
    x2: float = 0.0,
    y2: float = 0.0,
) -> bytes:
    """Encode a type-2 touch message, little-endian (StreamClient.kt:453-464).

    14 bytes for one pointer, 22 for two. ``action``: 0 = down, 1 = move,
    2 = up.
    """
    if count not in (1, 2):
        raise ValueError(f"pointer count must be 1 or 2, got {count}")
    out = struct.pack("<B", MessageType.TOUCH_EVENT) + struct.pack("<B", count)
    out += struct.pack("<f", x) + struct.pack("<f", y)
    if count == 2:
        out += struct.pack("<f", x2) + struct.pack("<f", y2)
    out += struct.pack("<i", action)
    return out


def encode_ping(ns: int) -> bytes:
    """Encode a type-4 ping carrying a little-endian i64 (StreamClient.kt:523)."""
    return struct.pack("<B", MessageType.PING) + struct.pack("<q", ns)


def decode_pong(payload: bytes) -> int:
    """Parse the 8 raw pong bytes as a little-endian i64 (StreamClient.kt:395)."""
    if len(payload) != 8:
        raise ProtocolError(f"pong payload must be 8 bytes, got {len(payload)}")
    (ns,) = struct.unpack("<q", payload)
    return ns


def encode_keyframe_request(force: bool) -> bytes:
    """Encode the 2-byte type-7 message; bit 0 forces an IDR (StreamClient.kt:502)."""
    flags = KEYFRAME_REQUEST_FLAG_FORCE if force else 0
    return bytes((MessageType.KEYFRAME_REQUEST, flags))


def encode_device_info(model: str, max_hz: int) -> bytes:
    """Encode the 66-byte type-11 message (StreamClient.kt:312-325).

    The model field is UTF-8 truncated on a character boundary to at most 63
    bytes so the final byte of the 64-byte field always stays zero.
    """
    if not 0 <= max_hz <= 255:
        raise ValueError(f"max_hz must be 0..255, got {max_hz}")
    raw = model.encode("utf-8")
    if len(raw) > 63:
        cut = raw[:63]
        while True:
            try:
                cut.decode("utf-8")
                break
            except UnicodeDecodeError:
                cut = cut[:-1]
        raw = cut
    field = raw + b"\x00" * (64 - len(raw))
    return bytes((MessageType.CLIENT_DEVICE_INFO,)) + field + struct.pack("B", max_hz)


def encode_capability(byte: int) -> bytes:
    """Encode a payload-free capability byte (types 8, 9, 12 outbound)."""
    if not isinstance(byte, int) or not 0 <= byte <= 255:
        raise ValueError(f"capability byte must be 0..255, got {byte!r}")
    return bytes((byte,))


def is_sync_frame_annexb(payload: bytes) -> bool:
    """Detect a sync frame in a legacy (type-0) Annex-B payload.

    Direct port of ``StreamClient.isSyncFrame`` (StreamClient.kt:690-735):
    HEVC IRAP NAL types 16..21 via ``(header & 0x7E) >> 1``, or H.264 IDR
    via ``(header & 0x1F) == 5``. Used only on the legacy path; type-6
    frames carry an explicit flag.
    """
    size = len(payload)
    i = 0
    while i + 5 < size:
        start = -1
        start_len = 0
        while i + 3 < size:
            if payload[i] == 0 and payload[i + 1] == 0:
                if payload[i + 2] == 1:
                    start = i
                    start_len = 3
                    break
                if i + 3 < size and payload[i + 2] == 0 and payload[i + 3] == 1:
                    start = i
                    start_len = 4
                    break
            i += 1
        if start < 0:
            return False
        nal_start = start + start_len
        if nal_start + 1 >= size:
            return False
        header = payload[nal_start]
        if ((header & 0x7E) >> 1) in range(16, 22) or (header & 0x1F) == 5:
            return True
        i = nal_start + 2
    return False


class FrameParser:
    """Incremental host-to-client message parser.

    Feed arbitrary TCP chunks via :meth:`feed`; complete messages are yielded
    as events in wire order. Raises :class:`ProtocolError` on oversize frames
    (``<= 0`` or ``> MAX_FRAME_SIZE``, mirroring the StreamClient.kt:557-559
    guard) or on unknown/unexpected message types (mirroring the
    StreamClient.kt:420-427 disconnect). The parser holds no more than the
    current incomplete message; it is single-threaded (one per connection)
    and must be discarded after a :class:`ProtocolError`, like the Android
    client dropping a misaligned stream.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> Iterator[Event]:
        self._buf += data
        buf = self._buf
        while True:
            if len(buf) < 1:
                return
            try:
                mtype = MessageType(buf[0])
            except ValueError:
                raise ProtocolError(f"unknown message type: {buf[0]}") from None

            if mtype is MessageType.SERVER_SHUTDOWN:
                del buf[0:1]
                yield ServerShutdownEvent()
                continue

            if mtype is MessageType.DEVICE_INFO_CAPABILITY:
                # Host's accept reply to our type-12 offer
                # (StreamingServer.swift:644-651).
                del buf[0:1]
                yield DeviceInfoAcceptedEvent()
                continue

            if mtype is MessageType.PONG:
                if len(buf) < 9:
                    return
                (sent_ns,) = struct.unpack_from("<q", buf, 1)
                del buf[0:9]
                yield PongEvent(sent_ns=sent_ns)
                continue

            if mtype is MessageType.DISPLAY_CONFIG:
                if len(buf) < 13:
                    return
                width, height, rotation = struct.unpack_from(">iii", buf, 1)
                del buf[0:13]
                yield DisplayConfigEvent(
                    width=width, height=height, rotation=rotation
                )
                continue

            if mtype is MessageType.CODEC_SELECTED:
                if len(buf) < 2:
                    return
                codec_id = buf[1]
                del buf[0:2]
                # Wire ids: 0 = HEVC, 1 = H.264 (CodecLimits.swift:7-12);
                # any non-zero is treated as non-HEVC like StreamClient.kt:402.
                yield CodecSelectedEvent(is_hevc=(codec_id == 0))
                continue

            if mtype is MessageType.LEGACY_VIDEO_FRAME:
                if len(buf) < 5:
                    return
                (size,) = struct.unpack_from(">i", buf, 1)
                if size <= 0 or size > MAX_FRAME_SIZE:
                    raise ProtocolError(f"invalid frame size: {size}")
                if len(buf) < 5 + size:
                    return
                payload = bytes(buf[5 : 5 + size])
                del buf[0 : 5 + size]
                yield FrameEvent(
                    payload=payload,
                    is_keyframe=is_sync_frame_annexb(payload),
                    capture_timestamp=0,
                )
                continue

            if mtype is MessageType.VIDEO_FRAME_WITH_METADATA:
                if len(buf) < 14:
                    return
                (size,) = struct.unpack_from(">i", buf, 1)
                if size <= 0 or size > MAX_FRAME_SIZE:
                    raise ProtocolError(f"invalid frame size: {size}")
                if len(buf) < 14 + size:
                    return
                flags = buf[5]
                (capture_ts,) = struct.unpack_from(">Q", buf, 6)
                payload = bytes(buf[14 : 14 + size])
                del buf[0 : 14 + size]
                yield FrameEvent(
                    payload=payload,
                    is_keyframe=bool(flags & FRAME_FLAG_KEYFRAME),
                    capture_timestamp=capture_ts,
                )
                continue

            # Remaining enum members (2, 4, 7, 8, 9, 11) are client-to-host
            # only and must never arrive inbound; treat as misalignment.
            raise ProtocolError(
                f"unexpected inbound message type: {mtype.name} ({mtype.value})"
            )
