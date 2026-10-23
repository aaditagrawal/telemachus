"""Unit tests for protocol encode/decode helpers and message layouts."""

import base64
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemachus_client.protocol import (
    FRAME_FLAG_KEYFRAME,
    KEYFRAME_REQUEST_FLAG_FORCE,
    MAGIC_REQUEST,
    MAGIC_RESPONSE,
    MAX_FRAME_SIZE,
    CodecSelectedEvent,
    DeviceInfoAcceptedEvent,
    DisplayConfigEvent,
    FrameEvent,
    FrameParser,
    HandshakeStatus,
    MessageType,
    PongEvent,
    ProtocolError,
    ServerShutdownEvent,
    decode_pong,
    encode_capability,
    encode_device_info,
    encode_handshake,
    encode_keyframe_request,
    encode_ping,
    encode_touch,
    parse_handshake_response,
    parse_qr,
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class ConstantsTest(unittest.TestCase):
    def test_message_type_numbers(self):
        self.assertEqual(MessageType.LEGACY_VIDEO_FRAME, 0)
        self.assertEqual(MessageType.DISPLAY_CONFIG, 1)
        self.assertEqual(MessageType.TOUCH_EVENT, 2)
        self.assertEqual(MessageType.SERVER_SHUTDOWN, 3)
        self.assertEqual(MessageType.PING, 4)
        self.assertEqual(MessageType.PONG, 5)
        self.assertEqual(MessageType.VIDEO_FRAME_WITH_METADATA, 6)
        self.assertEqual(MessageType.KEYFRAME_REQUEST, 7)
        self.assertEqual(MessageType.CLIENT_SUPPORTS_FRAME_METADATA, 8)
        self.assertEqual(MessageType.CLIENT_AVC_ONLY, 9)
        self.assertEqual(MessageType.CODEC_SELECTED, 10)
        self.assertEqual(MessageType.CLIENT_DEVICE_INFO, 11)
        self.assertEqual(MessageType.DEVICE_INFO_CAPABILITY, 12)

    def test_magic_and_limits(self):
        self.assertEqual(MAGIC_REQUEST, b"SSWA")
        self.assertEqual(MAGIC_RESPONSE, b"SSWR")
        self.assertEqual(MAX_FRAME_SIZE, 5 * 1024 * 1024)
        self.assertEqual(FRAME_FLAG_KEYFRAME, 1)
        self.assertEqual(KEYFRAME_REQUEST_FLAG_FORCE, 1)


class HandshakeTest(unittest.TestCase):
    def test_encode_shape(self):
        token = bytes(range(32))
        encoded = encode_handshake(token, "Test")
        self.assertEqual(encoded, b"SSWA" + token + b"\x04Test")
        self.assertEqual(len(encoded), 37 + 4)

    def test_encode_name_length_counts_utf8_bytes(self):
        encoded = encode_handshake(bytes(32), "é")  # 2 UTF-8 bytes
        self.assertEqual(encoded[36:37], b"\x02")
        self.assertEqual(encoded[37:], "é".encode("utf-8"))

    def test_encode_boundaries(self):
        encode_handshake(bytes(32), "x")
        encode_handshake(bytes(32), "n" * 64)
        with self.assertRaises(ValueError):
            encode_handshake(bytes(31), "x")
        with self.assertRaises(ValueError):
            encode_handshake(bytes(33), "x")
        with self.assertRaises(ValueError):
            encode_handshake(bytes(32), "")
        with self.assertRaises(ValueError):
            encode_handshake(bytes(32), "n" * 65)

    def test_parse_all_statuses(self):
        self.assertEqual(
            parse_handshake_response(b"SSWR\x00"), HandshakeStatus.OK
        )
        self.assertEqual(
            parse_handshake_response(b"SSWR\x01"), HandshakeStatus.INVALID_TOKEN
        )
        self.assertEqual(
            parse_handshake_response(b"SSWR\x02"), HandshakeStatus.INVALID_MAGIC
        )
        self.assertEqual(
            parse_handshake_response(b"SSWR\x03"), HandshakeStatus.INVALID_NAME
        )

    def test_parse_bad_magic_and_length(self):
        with self.assertRaises(ProtocolError):
            parse_handshake_response(b"XXXX\x00")
        with self.assertRaises(ProtocolError):
            parse_handshake_response(b"SSWA\x00")
        with self.assertRaises(ProtocolError):
            parse_handshake_response(b"SSWR")
        with self.assertRaises(ProtocolError):
            parse_handshake_response(b"SSWR\x00\x00")
        with self.assertRaises(ProtocolError):
            parse_handshake_response(b"")
        with self.assertRaises(ProtocolError):
            parse_handshake_response(b"SSWR\x07")


class QrTest(unittest.TestCase):
    def test_valid(self):
        token = bytes(range(32))
        info = parse_qr(f"telemachus://192.168.1.5:54321?t={_b64url(token)}&name=Mac%20Book")
        self.assertEqual(info.host, "192.168.1.5")
        self.assertEqual(info.port, 54321)
        self.assertEqual(info.token, token)
        self.assertEqual(info.name, "Mac Book")

    def test_default_name(self):
        token = bytes(range(32))
        info = parse_qr(f"telemachus://mac.local:1234?t={_b64url(token)}")
        self.assertEqual(info.name, "Mac")

    def test_bad_scheme(self):
        with self.assertRaises(ProtocolError):
            parse_qr("https://host:54321?t=AAAA&name=x")

    def test_missing_port(self):
        token = bytes(range(32))
        with self.assertRaises(ProtocolError):
            parse_qr(f"telemachus://host?t={_b64url(token)}")

    def test_bad_ports(self):
        token = bytes(range(32))
        with self.assertRaises(ProtocolError):
            parse_qr(f"telemachus://host:0?t={_b64url(token)}")
        with self.assertRaises(ProtocolError):
            parse_qr(f"telemachus://host:70000?t={_b64url(token)}")
        with self.assertRaises(ProtocolError):
            parse_qr(f"telemachus://host:abc?t={_b64url(token)}")

    def test_token_lengths(self):
        with self.assertRaises(ProtocolError):
            parse_qr("telemachus://h:1?t=" + _b64url(bytes(16)))
        with self.assertRaises(ProtocolError):
            parse_qr("telemachus://h:1?t=" + _b64url(bytes(33)))
        with self.assertRaises(ProtocolError):
            parse_qr("telemachus://h:1?t=!!!not-base64!!!")
        with self.assertRaises(ProtocolError):
            parse_qr("telemachus://h:1?name=x")


class TouchTest(unittest.TestCase):
    def test_count1_golden_14_bytes(self):
        # Hard golden: type + count + LE f32(1.0) + LE f32(2.0) + LE i32(2).
        encoded = encode_touch(1.0, 2.0, 2)
        self.assertEqual(len(encoded), 14)
        self.assertEqual(
            encoded,
            bytes.fromhex("02 01 00 00 80 3f 00 00 00 40 02 00 00 00"),
        )

    def test_count1_layout(self):
        encoded = encode_touch(0.5, 0.25, 1)
        self.assertEqual(len(encoded), 14)
        self.assertEqual(encoded[0], 2)
        self.assertEqual(encoded[1], 1)
        (x,) = struct.unpack_from("<f", encoded, 2)
        (y,) = struct.unpack_from("<f", encoded, 6)
        (action,) = struct.unpack_from("<i", encoded, 10)
        self.assertAlmostEqual(x, 0.5)
        self.assertAlmostEqual(y, 0.25)
        self.assertEqual(action, 1)

    def test_count2_golden_22_bytes(self):
        encoded = encode_touch(0.1, 0.2, 0, count=2, x2=0.3, y2=0.4)
        self.assertEqual(len(encoded), 22)
        self.assertEqual(encoded[0], 2)
        self.assertEqual(encoded[1], 2)
        (x, y, x2, y2) = struct.unpack("<ffff", encoded[2:18])
        (action,) = struct.unpack("<i", encoded[18:22])
        self.assertAlmostEqual(x, 0.1, places=5)
        self.assertAlmostEqual(y, 0.2, places=5)
        self.assertAlmostEqual(x2, 0.3, places=5)
        self.assertAlmostEqual(y2, 0.4, places=5)
        self.assertEqual(action, 0)

    def test_bad_count(self):
        with self.assertRaises(ValueError):
            encode_touch(0, 0, 0, count=0)
        with self.assertRaises(ValueError):
            encode_touch(0, 0, 0, count=3)


class PingPongTest(unittest.TestCase):
    def test_ping_golden(self):
        self.assertEqual(encode_ping(1), bytes.fromhex("04 01 00 00 00 00 00 00 00"))
        self.assertEqual(len(encode_ping(2**62)), 9)

    def test_pong_little_endian(self):
        ns = 1234567890123456789
        self.assertEqual(decode_pong(struct.pack("<q", ns)), ns)
        self.assertEqual(
            decode_pong(bytes.fromhex("08 07 06 05 04 03 02 01")),
            0x0102030405060708,
        )

    def test_pong_bad_length(self):
        with self.assertRaises(ProtocolError):
            decode_pong(b"\x01\x02\x03")
        with self.assertRaises(ProtocolError):
            decode_pong(b"\x00" * 9)


class KeyframeAndCapabilityTest(unittest.TestCase):
    def test_keyframe_request(self):
        self.assertEqual(encode_keyframe_request(False), b"\x07\x00")
        self.assertEqual(encode_keyframe_request(True), b"\x07\x01")

    def test_capability_bytes(self):
        self.assertEqual(encode_capability(8), b"\x08")
        self.assertEqual(encode_capability(9), b"\x09")
        self.assertEqual(encode_capability(12), b"\x0c")
        with self.assertRaises(ValueError):
            encode_capability(256)
        with self.assertRaises(ValueError):
            encode_capability(-1)


class DeviceInfoTest(unittest.TestCase):
    def test_shape_66_bytes(self):
        encoded = encode_device_info("Pixel", 120)
        self.assertEqual(len(encoded), 66)
        self.assertEqual(encoded[0], 11)
        self.assertEqual(encoded[1:6], b"Pixel")
        self.assertEqual(encoded[1:65][-1:], b"\x00")
        self.assertTrue(all(b == 0 for b in encoded[6:65]))
        self.assertEqual(encoded[65], 120)

    def test_truncation_keeps_last_byte_zero(self):
        encoded = encode_device_info("M" * 100, 60)
        self.assertEqual(len(encoded), 66)
        self.assertEqual(encoded[1:64], b"M" * 63)
        self.assertEqual(encoded[64], 0)
        self.assertEqual(encoded[65], 60)

    def test_multibyte_truncation_stays_valid_utf8(self):
        encoded = encode_device_info("é" * 40, 90)  # 80 UTF-8 bytes
        model = encoded[1:65].split(b"\x00")[0]
        self.assertLessEqual(len(model), 63)
        model.decode("utf-8")  # must not raise
        self.assertEqual(encoded[64], 0)

    def test_bad_hz(self):
        with self.assertRaises(ValueError):
            encode_device_info("x", 256)
        with self.assertRaises(ValueError):
            encode_device_info("x", -1)


class ParserMessageTest(unittest.TestCase):
    def test_display_config_big_endian(self):
        parser = FrameParser()
        events = list(
            parser.feed(
                bytes((1,)) + struct.pack(">iii", 1920, 1080, 90)
            )
        )
        self.assertEqual(events, [DisplayConfigEvent(1920, 1080, 90)])

    def test_legacy_frame_big_endian_size(self):
        parser = FrameParser()
        payload = b"\x00\x00\x00\x01\x65" + b"\x00" * 32
        events = list(parser.feed(bytes((0,)) + struct.pack(">i", len(payload)) + payload))
        self.assertEqual(len(events), 1)
        event = events[0]
        assert isinstance(event, FrameEvent)
        self.assertEqual(event.payload, payload)
        self.assertEqual(event.capture_timestamp, 0)

    def test_frame_with_metadata_big_endian(self):
        parser = FrameParser()
        payload = b"PAYLOAD!"
        wire = (
            bytes((6,))
            + struct.pack(">i", len(payload))
            + bytes((FRAME_FLAG_KEYFRAME,))
            + struct.pack(">Q", 987654321)
            + payload
        )
        events = list(parser.feed(wire))
        self.assertEqual(
            events,
            [FrameEvent(payload=payload, is_keyframe=True, capture_timestamp=987654321)],
        )

    def test_frame_metadata_flag_cleared(self):
        parser = FrameParser()
        payload = b"\x00\x00\x00\x01\x02" + b"data"
        wire = (
            bytes((6,))
            + struct.pack(">i", len(payload))
            + b"\x00"
            + struct.pack(">Q", 1)
            + payload
        )
        (event,) = parser.feed(wire)
        assert isinstance(event, FrameEvent)
        self.assertFalse(event.is_keyframe)

    def test_pong_event_little_endian(self):
        parser = FrameParser()
        ns = 555666777888999
        (event,) = parser.feed(bytes((5,)) + struct.pack("<q", ns))
        self.assertEqual(event, PongEvent(sent_ns=ns))

    def test_codec_selected(self):
        (hevc,) = FrameParser().feed(bytes((10, 0)))
        (avc,) = FrameParser().feed(bytes((10, 1)))
        self.assertEqual(hevc, CodecSelectedEvent(is_hevc=True))
        self.assertEqual(avc, CodecSelectedEvent(is_hevc=False))

    def test_shutdown_and_capability_accept(self):
        parser = FrameParser()
        events = list(parser.feed(bytes((12, 3))))
        self.assertEqual(
            events, [DeviceInfoAcceptedEvent(), ServerShutdownEvent()]
        )


if __name__ == "__main__":
    unittest.main()
