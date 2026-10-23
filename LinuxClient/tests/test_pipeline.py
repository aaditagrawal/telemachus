"""Run authenticated TCP, real HEVC decoding, and SDL presentation together."""

import os
import shutil
import subprocess
import time

import pytest

from telemachus_client import __main__ as cli
from telemachus_client import display, mockserver


@pytest.mark.parametrize("logical,output", [((64, 48), (64, 48)), ((65, 49), (66, 50))])
def test_authenticated_stream_reaches_sdl(monkeypatch, logical, output):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for the video pipeline")
    pytest.importorskip("sdl2")
    encoders = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout
    if "libx265" not in encoders:
        pytest.skip("the ffmpeg build has no HEVC encoder")
    payload = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
            f"color=c=blue:s={logical[0] * 2}x{logical[1] * 2}:r=30",
            "-frames:v", "1", "-c:v", "libx265",
            "-preset", "ultrafast", "-x265-params",
            "log-level=error:pools=1:frame-threads=1", "-f", "hevc", "pipe:1",
        ],
        capture_output=True, check=True, timeout=15,
    ).stdout
    expected_frame = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "hevc", "-i", "pipe:0",
            "-vf", f"scale={output[0]}:{output[1]}", "-frames:v", "1", "-pix_fmt", "nv12",
            "-f", "rawvideo", "pipe:1",
        ],
        input=payload, capture_output=True, check=True, timeout=15,
    ).stdout
    assert len(expected_frame) == output[0] * output[1] * 3 // 2
    monkeypatch.setenv("SDL_VIDEODRIVER", "offscreen")
    # Send a decodable access unit through the existing protocol mock. Coded
    # pixels are twice the advertised dimensions, matching a HiDPI Mac host.
    monkeypatch.setattr(mockserver, "generate_synthetic_hevc_clip", lambda **_: [(payload, True)])
    shown = []
    deadline = time.monotonic() + 10

    class ObservedDisplay(display.SdlDisplay):
        def poll(self):
            super().poll()
            if time.monotonic() > deadline:
                self.should_quit = True

        def present(self, frame):
            super().present(frame)
            shown.append(frame)
            if len(shown) >= 3:
                self.should_quit = True

    monkeypatch.setattr(display, "SdlDisplay", ObservedDisplay)
    token = os.urandom(32)
    server = mockserver.MockTelemachusServer(token, width=logical[0], height=logical[1])
    port = server.start()
    cfg = cli.ResolvedConfig(
        host="127.0.0.1", port=port, token=token, name="pipeline-test",
        decoder="sw", display=None, fullscreen=False, input_enabled=False,
    )
    try:
        assert cli.run_client(cli.build_arg_parser().parse_args([]), cfg) == 0
    finally:
        server.stop()
    assert shown == [expected_frame] * 3
    assert server.stats["connected_events"] == 1
