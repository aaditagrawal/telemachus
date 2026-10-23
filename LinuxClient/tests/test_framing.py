"""Unit tests for FrameParser incremental framing and rejection paths."""

import os
import random
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemachus_client.protocol import (
    MAX_FRAME_SIZE,
    CodecSelectedEvent,
    DisplayConfigEvent,
    FrameEvent,
    FrameParser,
    PongEvent,
    ProtocolError,
    ServerShutdownEvent,
    is_sync_frame_annexb,
)


def _display(w=1920, h=1080, r=0):
    return bytes((1,)) + struct.pack(">iii", w, h, r)


def _legacy(payload: bytes):
    return bytes((0,)) + struct.pack(">i", len(payload)) + payload


def _meta(payload: bytes, keyframe: bool, ts: int):
    return (
        bytes((6,))
        + struct.pack(">i", len(payload))
        + bytes((1 if keyframe else 0,))
        + struct.pack(">Q", ts)
        + payload
    )


def _pong(ns: int):
    return bytes((5,)) + struct.pack("<q", ns)


def _idr_payload(size: int = 64) -> bytes:
    return b"\x00\x00\x00\x01\x65" + bytes(size)


def _trail_payload(size: int = 64) -> bytes:
    return b"\x00\x00\x00\x01\x41" + bytes(size)


class FragmentationTest(unittest.TestCase):
    def setUp(self):
        self.stream = (
            _display(1920, 1080, 90)
            + _meta(b"frame-one", True, 111)
            + _pong(222)
            + _legacy(_idr_payload())
            + bytes((10, 0))
            + _meta(b"frame-two", False, 333)
            + bytes((3,))
        )
        self.expected = [
            DisplayConfigEvent(1920, 1080, 90),
            FrameEvent(b"frame-one", True, 111),
            PongEvent(222),
            FrameEvent(_idr_payload(), True, 0),
            CodecSelectedEvent(True),
            FrameEvent(b"frame-two", False, 333),
            ServerShutdownEvent(),
        ]

    def test_one_byte_at_a_time(self):
        parser = FrameParser()
        got = []
        for i in range(len(self.stream)):
            got.extend(parser.feed(self.stream[i : i + 1]))
        self.assertEqual(got, self.expected)

    def test_odd_splits(self):
        for cut in (1, 2, 3, 4, 5, 6, 12, 13, 14, 15, 27):
            with self.subTest(cut=cut):
                parser = FrameParser()
                got = list(parser.feed(self.stream[:cut]))
                got.extend(parser.feed(self.stream[cut:]))
                self.assertEqual(got, self.expected)

    def test_coalesced_single_feed(self):
        self.assertEqual(list(FrameParser().feed(self.stream)), self.expected)

    def test_large_frame_many_recvs(self):
        payload = os.urandom(1024 * 1024)
        wire = _meta(payload, True, 42)
        parser = FrameParser()
        got = []
        for i in range(0, len(wire), 1400):
            got.extend(parser.feed(wire[i : i + 1400]))
        self.assertEqual(got, [FrameEvent(payload, True, 42)])

    def test_empty_feeds_yield_nothing(self):
        parser = FrameParser()
        self.assertEqual(list(parser.feed(b"")), [])
        self.assertEqual(list(parser.feed(_display()[:7])), [])
        self.assertEqual(list(parser.feed(_display()[7:])), [DisplayConfigEvent(1920, 1080, 0)])

    def test_fragmentation_fuzz(self):
        rng = random.Random(1234)
        messages = []
        expected = []
        for i in range(60):
            kind = rng.randrange(6)
            if kind == 0:
                messages.append(_display(rng.randrange(4096), rng.randrange(4096), rng.choice([0, 90, 180, 270])))
                expected.append(DisplayConfigEvent(*struct.unpack(">iii", messages[-1][1:])))
            elif kind == 1:
                payload = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))
                messages.append(_legacy(payload))
                expected.append(FrameEvent(payload, is_sync_frame_annexb(payload), 0))
            elif kind == 2:
                payload = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))
                key = bool(rng.getrandbits(1))
                ts = rng.getrandbits(64)
                messages.append(_meta(payload, key, ts))
                expected.append(FrameEvent(payload, key, ts))
            elif kind == 3:
                ns = rng.getrandbits(63)
                messages.append(_pong(ns))
                expected.append(PongEvent(ns))
            elif kind == 4:
                raw = rng.choice([0, 1])
                messages.append(bytes((10, raw)))
                expected.append(CodecSelectedEvent(raw == 0))
            else:
                messages.append(_display())
                expected.append(DisplayConfigEvent(1920, 1080, 0))
        stream = b"".join(messages)
        parser = FrameParser()
        got = []
        pos = 0
        cuts = []
        while pos < len(stream):
            step = rng.randrange(1, 200)
            cuts.append(stream[pos : pos + step])
            pos += step
        for piece in cuts:
            got.extend(parser.feed(piece))
        self.assertEqual(got, expected)


class RejectionTest(unittest.TestCase):
    def test_oversize_frame(self):
        parser = FrameParser()
        with self.assertRaises(ProtocolError):
            list(parser.feed(bytes((0,)) + struct.pack(">i", MAX_FRAME_SIZE + 1)))

    def test_oversize_meta_frame(self):
        parser = FrameParser()
        wire = bytes((6,)) + struct.pack(">i", MAX_FRAME_SIZE + 1) + b"\x01" + struct.pack(">Q", 0)
        with self.assertRaises(ProtocolError):
            list(parser.feed(wire))

    def test_zero_and_negative_sizes(self):
        for size in (0, -1, -2147483648):
            with self.subTest(size=size):
                parser = FrameParser()
                with self.assertRaises(ProtocolError):
                    list(parser.feed(bytes((0,)) + struct.pack(">i", size)))

    def test_unknown_type(self):
        parser = FrameParser()
        with self.assertRaises(ProtocolError):
            list(parser.feed(bytes((0x7F,))))

    def test_client_only_type_inbound(self):
        for bad in (2, 4, 7, 8, 9, 11):
            with self.subTest(bad=bad):
                with self.assertRaises(ProtocolError):
                    list(FrameParser().feed(bytes((bad,)) + b"\x00" * 70))

    def test_truncated_header_completes_later(self):
        parser = FrameParser()
        self.assertEqual(list(parser.feed(bytes((0,)) + struct.pack(">i", 3)[:2])), [])
        events = list(parser.feed(struct.pack(">i", 3)[2:] + b"abc"))
        self.assertEqual(events, [FrameEvent(b"abc", False, 0)])

    def test_header_split_inside_meta_timestamp(self):
        wire = _meta(b"hello!", True, 0x0102030405060708)
        parser = FrameParser()
        got = []
        got.extend(parser.feed(wire[:9]))
        got.extend(parser.feed(wire[9:]))
        self.assertEqual(got, [FrameEvent(b"hello!", True, 0x0102030405060708)])


class SyncScanTest(unittest.TestCase):
    def test_hevc_irap_detected(self):
        for nal in (16, 17, 18, 19, 20, 21):
            header = bytes(((nal << 1) & 0xFE, 0x01))
            self.assertTrue(
                is_sync_frame_annexb(b"\x00\x00\x00\x01" + header + b"\x00" * 16),
                f"HEVC NAL {nal}",
            )

    def test_hevc_non_irap_rejected(self):
        for nal in (0, 1, 2, 32, 33, 34):
            header = bytes(((nal << 1) & 0xFE, 0x01))
            self.assertFalse(
                is_sync_frame_annexb(b"\x00\x00\x00\x01" + header + b"\x00" * 16),
                f"HEVC NAL {nal}",
            )

    def test_h264_idr_detected(self):
        self.assertTrue(is_sync_frame_annexb(b"\x00\x00\x00\x01\x65" + b"\x00" * 16))
        self.assertTrue(is_sync_frame_annexb(b"\x00\x00\x01\x65" + b"\x00" * 16))

    def test_h264_non_idr_rejected(self):
        self.assertFalse(is_sync_frame_annexb(b"\x00\x00\x00\x01\x67" + b"\x00" * 16))
        self.assertFalse(is_sync_frame_annexb(b"\x00\x00\x00\x01\x41" + b"\x00" * 16))

    def test_no_start_code(self):
        self.assertFalse(is_sync_frame_annexb(b"\x01\x02\x03\x04\x05\x06\x07\x08"))

    def test_sync_after_non_sync_nal(self):
        stream = (
            b"\x00\x00\x00\x01\x41" + b"\x00" * 8
            + b"\x00\x00\x00\x01\x65" + b"\x00" * 8
        )
        self.assertTrue(is_sync_frame_annexb(stream))

    def test_legacy_parser_uses_scan(self):
        (key,) = FrameParser().feed(_legacy(_idr_payload()))
        (inter,) = FrameParser().feed(_legacy(_trail_payload()))
        assert isinstance(key, FrameEvent) and isinstance(inter, FrameEvent)
        self.assertTrue(key.is_keyframe)
        self.assertFalse(inter.is_keyframe)


if __name__ == "__main__":
    unittest.main()
