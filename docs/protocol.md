# Telemachus Wire Protocol

Status: implementation documentation for the current development branch. This
is not yet a stable compatibility promise.

Telemachus carries one full-duplex byte stream over TCP. USB mode reaches the
Mac listener through `adb reverse`; wireless mode connects directly over the
LAN. TCP preserves byte order but not application-message boundaries, so every
length-prefixed message must be parsed incrementally.

## Connection admission

Loopback connections are treated as USB and do not perform application-layer
authentication. This relies on ADB authorization and on the listener's
loopback admission rules.

Non-loopback connections require wireless mode and a 32-byte bearer token. The
client begins with:

```text
"SSWA" (4 bytes) | token (32 bytes) | name length (1 byte) | UTF-8 name (1..64 bytes)
```

The server responds:

```text
"SSWR" (4 bytes) | status (1 byte)
```

Status values are `0` success, `1` invalid token, `2` invalid magic, and `3`
invalid device name. A successful client may then send capability messages.
The authentication exchange is not encrypted or replay-resistant.

Pairing QR codes use:

```text
telemachus://HOST:PORT?t=BASE64URL_32_BYTE_TOKEN&name=URL_ENCODED_NAME
```

Anyone who obtains that URI has the wireless credential until it is reset.

## Message types

The first byte is the message type:

| Type | Direction | Meaning |
| ---: | --- | --- |
| 0 | Mac to Android | Legacy video frame |
| 1 | Mac to Android | Display configuration |
| 2 | Android to Mac | Touch event |
| 4 | Android to Mac | Ping |
| 5 | Mac to Android | Pong |
| 6 | Mac to Android | Video frame with capture metadata |
| 7 | Android to Mac | Keyframe request |
| 8 | Android to Mac | Client supports frame metadata |
| 9 | Android to Mac | Client has no HEVC decoder |
| 10 | Mac to Android | Selected codec |

Types 8 and 9 are opt-in capability messages. The host must not send type 10
unless the client sent type 9, because older clients disconnect on unknown
server messages.

Video and display payload fields use network byte order where the
implementation writes fixed-width integers. Refer to the codec implementations
and tests for the exact current field layout. Any protocol change must add a
cross-platform fixture test and describe old/new mixed-version behavior here.

## Backpressure and recovery

The host keeps at most one frame in the network send and one safe successor.
When preserving inter-frame dependencies is no longer possible, it drops the
backlog and requests a new sync frame. Clients may request a keyframe after
decoder loss or stale output.

## Compatibility policy

New client-to-host payload-free capability bytes are preferable when old hosts
can safely ignore them. New host-to-client messages must be negotiated first.
Unbounded lengths, platform-native integer layouts, and implicit TCP packet
boundaries are prohibited.
