"""Decoder regressions, including real H.264 at twice the display size."""

import re
import shutil
import subprocess
import sys
import threading
import time

import pytest

from telemachus_client import decode


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "condition did not become true"


@pytest.fixture(scope="module")
def h264_units():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg unavailable")
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc2=size=128x96:rate=10", "-frames:v", "10",
        "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
        "-tune", "zerolatency", "-g", "1", "-x264-params", "aud=1",
        "-pix_fmt", "yuv420p", "-f", "h264", "pipe:1",
    ], capture_output=True, timeout=10)
    if result.returncode and b"Unknown encoder" in result.stderr:
        pytest.skip("ffmpeg has no libx264 encoder")
    assert result.returncode == 0, result.stderr.decode()
    starts = [m.start() for m in re.finditer(b"\x00\x00\x00\x01\x09", result.stdout)]
    assert len(starts) == 10
    starts.append(len(result.stdout))
    return [result.stdout[a:b] for a, b in zip(starts, starts[1:])]


def finish_input(decoder):
    proc = decoder.proc
    assert proc is not None and proc.stdin is not None
    # Wait until the writer has completed every access unit before EOF.
    wait_for(lambda: decoder.stats.fed == 10)
    proc.stdin.close()
    wait_for(lambda: decoder.stats.decoded == 10)
    wait_for(decoder.is_dead)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("logical,output", [((64, 48), (64, 48)), ((65, 49), (66, 50))])
def test_real_hidpi_decode_scales_before_rotation(h264_units, rotation, logical, output):
    decoder = decode.FrameDecoder(*logical, False, decoder="sw", rotation=rotation,
                                  max_queue_frames=16)
    try:
        assert decoder.start() == "sw"
        for unit in h264_units:
            assert decoder.feed(unit, is_keyframe=True)
        finish_input(decoder)
        width, height = output[::-1] if rotation in (90, 270) else output
        assert (decoder.out_w, decoder.out_h) == (width, height)
        assert decoder.frame_len == width * height * 3 // 2
        filters = f"scale={output[0]}:{output[1]}"
        if rotation:
            filters += "," + decode.rotation_filter(rotation)
        expected = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "h264", "-i", "pipe:0",
            "-vf", filters, "-f", "rawvideo", "-pix_fmt", "nv12", "pipe:1",
        ], input=b"".join(h264_units), capture_output=True, check=True, timeout=10).stdout
        assert len(expected) == decoder.frame_len * 10
        assert decoder.read_newest() == expected[-decoder.frame_len:]
    finally:
        proc = decoder.proc
        workers = (decoder._reader, decoder._writer)
        decoder.close()
    assert proc.poll() is not None
    assert all(not worker.is_alive() for worker in workers)


def test_raw_nv12_size_includes_rounded_chroma_cells():
    assert decode.frame_size_bytes(1281, 801) == 1_540_163
    assert decode.output_dims(1281, 801, 0) == (1282, 802)
    assert decode.output_dims(1281, 801, 90) == (802, 1282)


def test_auto_advances_after_real_hardware_initialization_failure(h264_units, monkeypatch):
    original = decode.build_ffmpeg_cmd

    def failed_hardware(*args, **kwargs):
        argv, backend = original(*args, **kwargs)
        if backend in ("vaapi", "cuda"):
            # This is a real ffmpeg failure after Popen succeeds. Use an
            # invalid device so the test is deterministic on GPU machines.
            index = argv.index("-hwaccel")
            argv[index + 1] = "vaapi"
            if "-hwaccel_device" in argv:
                argv[argv.index("-hwaccel_device") + 1] = "/dev/telemachus-missing-gpu"
            else:
                argv[index:index] = ["-hwaccel_device", "/dev/telemachus-missing-gpu"]
        return argv, backend

    monkeypatch.setattr(decode, "build_ffmpeg_cmd", failed_hardware)
    decoder = decode.FrameDecoder(64, 48, False, decoder="auto",
                                  render_node="/dev/telemachus-missing-gpu",
                                  max_queue_frames=16)
    try:
        for backend in ("vaapi", "cuda"):
            assert decoder.start() == backend
            assert decoder.feed(b"".join(h264_units), is_keyframe=True)
            wait_for(decoder.is_dead)
            assert decoder.stats.decoded == 0
        assert decoder.start() == "sw"
        # fed is cumulative across failed attempts.
        decoder.stats.fed = 0
        for unit in h264_units:
            assert decoder.feed(unit, is_keyframe=True)
        finish_input(decoder)
        assert decoder.stats.decoded == 10
        assert decoder.start() == "sw"
        assert decoder.stats.restarts == 3
    finally:
        decoder.close()


class ShortStdin:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.output = bytearray()
        self.closed = False

    def write(self, data):
        self.started.set()
        assert self.release.wait(5), "test writer was never released"
        if self.closed:
            raise BrokenPipeError()
        count = min(3, len(data))
        self.output.extend(data[:count])
        return count

    def close(self):
        self.closed = True
        self.release.set()


class BlockingStdout:
    def __init__(self):
        self.release = threading.Event()

    def read(self, count):
        assert self.release.wait(5), "test reader was never released"
        return b""

    def close(self):
        self.release.set()


class FakeProcess:
    def __init__(self):
        self.stdin = ShortStdin()
        self.stdout = BlockingStdout()
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self.stdin.close()
        self.stdout.close()

    kill = terminate

    def wait(self, timeout):
        return self.returncode


@pytest.fixture
def fake_decoder(monkeypatch):
    proc = FakeProcess()
    monkeypatch.setattr(decode.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(decode.subprocess, "Popen", lambda *a, **kw: proc)
    decoder = decode.FrameDecoder(64, 48, False, decoder="sw", max_queue_frames=2,
                                  max_queue_bytes=64)
    decoder.start()
    yield decoder, proc
    decoder.close()


def test_feed_does_not_block_and_short_writes_preserve_access_units(fake_decoder):
    decoder, proc = fake_decoder
    unit1, unit2 = b"first-keyframe", b"next-frame"
    started = time.monotonic()
    assert decoder.feed(unit1, is_keyframe=True)
    assert time.monotonic() - started < 0.2
    assert proc.stdin.started.wait(1)
    assert decoder.feed(unit2)
    assert not proc.stdin.output
    proc.stdin.release.set()
    wait_for(lambda: decoder.stats.fed == 2)
    assert bytes(proc.stdin.output) == unit1 + unit2


def test_queue_overflow_drops_dependencies_until_keyframe(fake_decoder):
    decoder, proc = fake_decoder
    requests = []
    decoder.on_need_keyframe = lambda: requests.append(True)
    assert decoder.feed(b"inflight-keyframe", is_keyframe=True)
    assert proc.stdin.started.wait(1)
    assert decoder.feed(b"stale-1")
    assert decoder.feed(b"stale-2")
    started = time.monotonic()
    assert not decoder.feed(b"overflow")
    assert not decoder.feed(b"dependent")
    assert time.monotonic() - started < 0.2
    assert requests == [True]
    assert decoder.feed(b"fresh-keyframe", is_keyframe=True)
    assert decoder.feed(b"fresh-dependent")
    proc.stdin.release.set()
    wait_for(lambda: decoder.stats.fed == 3)
    assert bytes(proc.stdin.output) == b"inflight-keyframefresh-keyframefresh-dependent"


def test_queue_is_bounded_by_bytes_and_oversized_units_are_never_written(fake_decoder):
    decoder, proc = fake_decoder
    assert decoder.feed(b"keyframe", is_keyframe=True)
    assert proc.stdin.started.wait(1)
    assert decoder.feed(b"a" * 40)
    assert not decoder.feed(b"b" * 40)
    assert not decoder.feed(b"x" * 65, is_keyframe=True)
    assert not decoder.feed(b"dependent")
    assert decoder.feed(b"new-keyframe", is_keyframe=True)
    proc.stdin.release.set()
    wait_for(lambda: decoder.stats.fed == 2)
    assert bytes(proc.stdin.output) == b"keyframenew-keyframe"


def test_close_while_writer_and_reader_are_blocked_is_idempotent(fake_decoder):
    decoder, proc = fake_decoder
    assert decoder.feed(b"keyframe", is_keyframe=True)
    assert proc.stdin.started.wait(1)
    workers = (decoder._reader, decoder._writer)
    decoder.close()
    decoder.close()
    assert decoder.proc is None
    assert proc.poll() is not None
    assert all(not worker.is_alive() for worker in workers)
    assert not decoder.feed(b"after-close", is_keyframe=True)


def test_real_pipe_backpressure_does_not_block_feed_or_close(monkeypatch):
    popen = subprocess.Popen
    monkeypatch.setattr(decode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    def sleeping_process(*args, **kwargs):
        return popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

    monkeypatch.setattr(decode.subprocess, "Popen", sleeping_process)
    decoder = decode.FrameDecoder(64, 48, False, decoder="sw", max_queue_frames=2)
    try:
        decoder.start()
        unit = b"x" * (1024 * 1024)
        started = time.monotonic()
        assert decoder.feed(unit, is_keyframe=True)
        assert time.monotonic() - started < 0.2
        # Let the worker enter a pipe write that exceeds the pipe capacity.
        wait_for(lambda: decoder._session.queued_bytes == 0)
        assert decoder.feed(unit)
        assert decoder.feed(unit)
        started = time.monotonic()
        assert not decoder.feed(unit)
        assert time.monotonic() - started < 0.2
    finally:
        proc = decoder.proc
        workers = (decoder._reader, decoder._writer)
        started = time.monotonic()
        decoder.close()
    assert time.monotonic() - started < 2
    assert proc.poll() is not None
    assert all(not worker.is_alive() for worker in workers)


@pytest.fixture(scope="module")
def hevc_unit():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg unavailable")
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "color=c=blue:s=128x96:r=30", "-frames:v", "1",
        "-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
        "log-level=error:pools=1:frame-threads=1", "-f", "hevc", "pipe:1",
    ], capture_output=True, timeout=10)
    if result.returncode and b"Unknown encoder" in result.stderr:
        pytest.skip("ffmpeg has no libx265 encoder")
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


@pytest.mark.parametrize("is_hevc", [False, True])
def test_live_stream_outputs_without_eof_or_a_cpu_sized_frame_queue(
    h264_units, hevc_unit, is_hevc, monkeypatch,
):
    original = decode.build_ffmpeg_cmd

    def many_cpus(*args, **kwargs):
        argv, backend = original(*args, **kwargs)
        argv[1:1] = ["-cpucount", "192"]
        return argv, backend

    monkeypatch.setattr(decode, "build_ffmpeg_cmd", many_cpus)
    decoder = decode.FrameDecoder(64, 48, is_hevc, decoder="sw", max_queue_frames=16)
    try:
        decoder.start()
        started = time.monotonic()
        # Pace a short stream as a live host would. Leave stdin open: a
        # decoder buffering a CPU-sized frame queue never emits these frames.
        for unit in h264_units:
            assert decoder.feed(hevc_unit if is_hevc else unit, is_keyframe=True)
            time.sleep(1 / 30)
            if decoder.peek_seq():
                break
        wait_for(lambda: decoder.peek_seq() > 0, timeout=2)
        assert time.monotonic() - started < 2.5
        assert not decoder.proc.stdin.closed
        assert decoder.proc.poll() is None
        assert len(decoder.read_newest()) == 64 * 48 * 3 // 2
    finally:
        decoder.close()
