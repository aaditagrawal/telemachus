"""In-process integration test: real StreamClient against MockTelemachusServer.

Proves the seam with no guessing: the mock is started on the main thread via
``server.start()`` (which returns the bound ephemeral port), a real
``ClientConfig`` points at it, and the real ``StreamClient`` connects,
exchanges capabilities, receives display config + frames, answers RTT, and
requests keyframes. A bad-token connect must raise ``TokenRejectedError``.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemachus_client.config import ClientConfig
from telemachus_client.mockserver import MockTelemachusServer
from telemachus_client.netclient import StreamClient
from telemachus_client.protocol import TokenRejectedError

WIDTH, HEIGHT, ROTATION = 1280, 720, 0


def _wait_until(pred, timeout_s, interval_s=0.02):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval_s)
    return bool(pred())


class ClientMockSeamTest(unittest.TestCase):
    def test_stream_seam(self):
        token = os.urandom(32)
        server = MockTelemachusServer(
            token=token, width=WIDTH, height=HEIGHT, rotation=ROTATION,
            fake_payload=True,
        )
        port = server.start()
        self.assertNotEqual(port, 0)
        self.assertEqual(port, server.port)

        cfg = ClientConfig(
            host="127.0.0.1", port=port, token=token, name="integration-test",
        )

        frames = []
        displays = []
        rtts = []
        codecs = []
        states = []
        lock = threading.Lock()

        def on_display(w, h, rotation):
            with lock:
                displays.append((w, h, rotation))

        def on_frame(payload, is_keyframe, capture_ts):
            with lock:
                frames.append((bytes(payload), bool(is_keyframe), capture_ts))

        def on_codec(is_hevc):
            with lock:
                codecs.append(bool(is_hevc))

        def on_rtt(ms):
            with lock:
                rtts.append(float(ms))

        def on_state(connected):
            with lock:
                states.append(bool(connected))

        client = StreamClient(
            cfg,
            on_display=on_display,
            on_frame=on_frame,
            on_codec=on_codec,
            on_rtt=on_rtt,
            on_state=on_state,
        )

        # Tap client->server bytes to assert capability order on the wire.
        wire = bytearray()
        orig_send = client._send

        def tap(data):
            wire.extend(data)
            orig_send(data)

        client._send = tap  # type: ignore[method-assign]

        worker = None
        try:
            client.connect()
            client.send_capabilities(
                supports_metadata=True, avc_only=True,
                device_info=("TestModel", 60),
            )
            # Capabilities go out as one buffer: 9 before 8, then 12.
            self.assertEqual(bytes(wire[:3]), bytes((9, 8, 12)))

            worker = threading.Thread(target=client.run, daemon=True)
            worker.start()

            self.assertTrue(
                _wait_until(lambda: len(displays) >= 1, 5.0),
                "no displayConfig from mock",
            )
            with lock:
                self.assertIn((WIDTH, HEIGHT, ROTATION), displays)
            # Type 9 was honoured: codecSelected arrives before displayConfig.
            self.assertTrue(
                _wait_until(lambda: len(codecs) >= 1, 5.0),
                "no codecSelected from mock (type-9 capability not honoured)",
            )
            self.assertTrue(
                _wait_until(lambda: len(frames) >= 1, 5.0),
                "no frames from mock",
            )
            # Type-12 accept round trip: client answered with type-11 device info.
            self.assertTrue(
                _wait_until(lambda: len(server.stats["device_infos"]) >= 1, 5.0),
                "mock never received type-11 device info",
            )
            self.assertIn(("TestModel", 60), server.stats["device_infos"])

            self.assertGreaterEqual(server.stats["connected_events"], 1)

            client.send_ping()
            self.assertTrue(
                _wait_until(lambda: len(rtts) >= 1, 5.0),
                "no on_rtt after ping",
            )
            with lock:
                self.assertGreaterEqual(rtts[0], 0.0)

            client.request_keyframe(force=True)
            self.assertTrue(
                _wait_until(
                    lambda: server.stats["keyframe_requests"] >= 1, 5.0),
                "mock never saw the keyframe request",
            )
            self.assertGreaterEqual(server.stats["keyframe_forced"], 1)
        finally:
            client.close()
            if worker is not None:
                worker.join(timeout=5.0)
                self.assertFalse(worker.is_alive(), "client run thread leaked")

        with lock:
            self.assertIn(True, states)

        # Bad token against the same live mock must be rejected loudly.
        bad = StreamClient(
            ClientConfig(
                host="127.0.0.1", port=port, token=os.urandom(32),
                name="integration-test",
            )
        )
        with self.assertRaises(TokenRejectedError):
            bad.connect()

        server.stop()
        self.assertTrue(
            _wait_until(
                lambda: not any(
                    t.name.startswith("mock-") for t in threading.enumerate()
                ),
                5.0,
            ),
            "mock threads leaked after stop()",
        )


if __name__ == "__main__":
    unittest.main()
