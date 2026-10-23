"""ffmpeg subprocess decoder for the Telemachus Linux client.

Python owns the protocol and the SDL2 window; ffmpeg owns the actual
HEVC/H.264 decode. Annex-B bytes go in on stdin, raw frames come out on
stdout.

Pipeline (low latency, NV12 preferred)::

    ffmpeg -probesize 32 -analyzeduration 0 -fflags +flush_packets
           -flags low_delay [-hwaccel ...] -f hevc|h264 -i pipe:0
           -vf <scale/download chain> -f rawvideo -pix_fmt nv12 pipe:1

Hardware preference: VAAPI (Radeon Vega iGPU) -> NVDEC/CUDA (GTX 1660 Ti)
-> software. The VAAPI render node is enumerated from /dev/dri/renderD*
rather than hardcoded.

Threading model: the network reader thread calls :meth:`FrameDecoder.feed`
and must never block on the renderer. Decoded output is kept in a single
newest-frame slot; stale frames are dropped. The SDL render loop polls
with :meth:`FrameDecoder.read_newest`.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import BinaryIO, Callable, Literal, Optional

log = logging.getLogger("telemachus.decode")

DecoderPreference = Literal["auto", "vaapi", "cuda", "sw"]
PixelFormat = Literal["nv12", "bgra"]

#: Bytes per pixel for the raw formats we support on stdout.
BYTES_PER_PIXEL: dict[str, float] = {"nv12": 1.5, "bgra": 4.0}

#: Transpose chains that implement clockwise rotation inside ffmpeg.
_ROTATION_FILTERS: dict[int, str] = {
    0: "",
    90: "transpose=1",
    180: "transpose=1,transpose=1",
    270: "transpose=2",
}


def find_render_nodes() -> list[str]:
    """Return sorted /dev/dri/renderD* nodes, or []."""
    try:
        nodes: list[str] = sorted(glob.glob("/dev/dri/renderD*"))
    except OSError:
        return []
    return [n for n in nodes if os.path.exists(n)]


def ffmpeg_available() -> bool:
    """Return True when a system ffmpeg binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def frame_size_bytes(width: int, height: int, pix_fmt: PixelFormat = "nv12") -> int:
    """Return the raw stdout frame size for *width* x *height*."""
    if pix_fmt == "nv12":
        return width * height + 2 * ((width + 1) // 2) * ((height + 1) // 2)
    return int(width * height * BYTES_PER_PIXEL[pix_fmt])


def output_dims(width: int, height: int, rotation: int) -> tuple[int, int]:
    """Return even output dimensions after scaling and rotation.

    NV12 textures need complete 2x2 chroma cells. Custom logical dimensions
    can be odd, so round up before scaling rather than splitting raw frames
    at the wrong boundary or using an incompatible texture pitch.
    """
    width += width % 2
    height += height % 2
    if rotation % 360 in (90, 270):
        return (height, width)
    return (width, height)


def rotation_filter(rotation: int) -> str:
    """Return the ffmpeg transpose chain for *rotation* (best-effort)."""
    return _ROTATION_FILTERS.get(rotation % 360, "")


def build_ffmpeg_cmd(
    width: int,
    height: int,
    is_hevc: bool,
    decoder: DecoderPreference = "auto",
    render_node: Optional[str] = None,
    pix_fmt: PixelFormat = "nv12",
    rotation: int = 0,
) -> tuple[list[str], str]:
    """Build the ffmpeg argv for the stream and return (argv, backend).

    *backend* is one of ``"vaapi"``, ``"cuda"`` or ``"sw"`` and describes
    which acceleration path the argv uses. Raises :class:`FileNotFoundError`
    when no ffmpeg binary is on PATH.
    """
    if shutil.which("ffmpeg") is None:
        raise FileNotFoundError("ffmpeg not found on PATH (apt install ffmpeg)")

    width, height = output_dims(width, height, 0)
    codec_flag: str = "hevc" if is_hevc else "h264"
    filt: str = rotation_filter(rotation)
    backend: str = "sw"
    hw_pre: list[str] = []
    vf_chain: str = ""

    wants_vaapi: bool = decoder in ("auto", "vaapi")
    wants_cuda: bool = decoder in ("auto", "cuda")

    if decoder == "vaapi" or (wants_vaapi and decoder == "auto"):
        nodes: list[str] = [render_node] if render_node else find_render_nodes()
        # In explicit vaapi mode keep going even with no node so the error
        # surfaces from ffmpeg; in auto mode fall through to cuda/sw.
        if nodes or decoder == "vaapi":
            node: str = nodes[0] if nodes else (render_node or "/dev/dri/renderD128")
            hw_pre = [
                "-hwaccel", "vaapi",
                "-hwaccel_device", node,
                "-hwaccel_output_format", "vaapi",
            ]
            vf_chain = "scale_vaapi=format=nv12,hwdownload,format=nv12"
            backend = "vaapi"
        else:
            hw_pre = []
    if backend == "sw" and wants_cuda and decoder in ("auto", "cuda"):
        hw_pre = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        vf_chain = "scale_cuda=format=nv12,hwdownload,format=nv12"
        backend = "cuda"
    if decoder == "sw":
        hw_pre = []
        vf_chain = ""
        backend = "sw"
    if decoder == "vaapi" and backend != "vaapi":
        # Explicit request but no render node enumerated; still emit a
        # VAAPI command so ffmpeg reports the real error.
        hw_pre = [
            "-hwaccel", "vaapi",
            "-hwaccel_device", render_node or "/dev/dri/renderD128",
            "-hwaccel_output_format", "vaapi",
        ]
        vf_chain = "scale_vaapi=format=nv12,hwdownload,format=nv12"
        backend = "vaapi"

    # The host advertises logical display dimensions, while a HiDPI stream
    # can contain twice as many coded pixels in each direction. Size the raw
    # output explicitly before rotating so stdout always matches frame_len.
    filters: list[str] = []
    if vf_chain:
        filters.append(vf_chain)
    filters.append(f"scale={width}:{height}")
    if filt:
        filters.append(filt)
    # Pixel format conversion when the caller asked for BGRA debug output.
    if pix_fmt == "bgra" and backend in ("vaapi", "cuda"):
        # After hwdownload the frames are already in system memory, so a
        # plain format filter suffices.
        filters.append("format=bgra")

    argv: list[str] = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-probesize", "32", "-analyzeduration", "0",
        "-fflags", "+flush_packets", "-flags", "low_delay",
        # Bound frame-thread delay independently of host CPU count. Raw
        # output needs no encoder frame queue; filters can split one frame
        # between two workers without queuing future frames.
        "-threads", "2", "-filter_threads", "2",
    ]
    argv += hw_pre
    argv += ["-f", codec_flag, "-i", "pipe:0"]
    if filters:
        argv += ["-vf", ",".join(filters)]
    argv += ["-threads", "1", "-f", "rawvideo", "-pix_fmt", pix_fmt, "pipe:1"]
    return argv, backend


@dataclass
class DecoderStats:
    fed: int = 0
    decoded: int = 0
    dropped_stale: int = 0
    dropped_input: int = 0
    restarts: int = 0


@dataclass
class _DecoderSession:
    # Workers retain their own process and stop event across a close/restart.
    proc: subprocess.Popen[bytes]
    stop: threading.Event = field(default_factory=threading.Event)
    dead: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    pending: deque[bytes] = field(default_factory=deque)
    queued_bytes: int = 0
    waiting_keyframe: bool = True


class FrameDecoder:
    """Decode Annex-B with a bounded input queue and newest-frame output.

    ``start`` and ``close`` serialize lifecycle changes. ``feed`` only queues
    complete access units and never writes to a pipe. After a dropped unit,
    dependent frames are discarded until a keyframe arrives. The optional
    ``on_need_keyframe`` callback must return quickly, e.g. set an event.
    """

    def __init__(
        self,
        width: int,
        height: int,
        is_hevc: bool,
        decoder: DecoderPreference = "auto",
        render_node: Optional[str] = None,
        pix_fmt: PixelFormat = "nv12",
        rotation: int = 0,
        on_need_keyframe: Optional[Callable[[], None]] = None,
        max_queue_frames: int = 8,
        max_queue_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if max_queue_frames < 1 or max_queue_bytes < 1:
            raise ValueError("decoder queue limits must be positive")
        self.width = width
        self.height = height
        self.is_hevc = is_hevc
        self.decoder_pref = decoder
        self.render_node = render_node
        self.pix_fmt = pix_fmt
        self.rotation = rotation % 360
        self.on_need_keyframe = on_need_keyframe
        self.max_queue_frames = max_queue_frames
        self.max_queue_bytes = max_queue_bytes
        self.out_w, self.out_h = output_dims(width, height, self.rotation)
        self.frame_len = frame_size_bytes(self.out_w, self.out_h, pix_fmt)

        if decoder == "auto":
            self._backends: list[DecoderPreference] = (
                (["vaapi"] if render_node or find_render_nodes() else []) + ["cuda", "sw"]
            )
        else:
            self._backends = [decoder] + (["sw"] if decoder != "sw" else [])
        self._backend_index = 0
        self.backend: str = "sw"
        self.proc: Optional[subprocess.Popen[bytes]] = None
        self._session: Optional[_DecoderSession] = None
        self._reader: Optional[threading.Thread] = None
        self._writer: Optional[threading.Thread] = None
        self._lifecycle_lock = threading.Lock()
        self._lock = threading.Lock()
        self._slot: Optional[bytes] = None
        self._slot_seq = 0
        self.stats = DecoderStats()

    def start(self) -> str:
        """Start or restart ffmpeg, retaining failed-backend progression.

        Hardware initialization happens after Popen, once input arrives.
        Restarting a dead session advances to the next backend. Software
        remains the final choice for later restarts of this decoder.
        """
        with self._lifecycle_lock:
            if self._session is not None:
                if not self.is_dead():
                    return self.backend
                self._backend_index = min(self._backend_index + 1, len(self._backends) - 1)
                self.stats.restarts += 1
                self._close_locked()
            while True:
                pref = self._backends[self._backend_index]
                argv, backend = build_ffmpeg_cmd(
                    self.width, self.height, self.is_hevc,
                    pref, self.render_node, self.pix_fmt, self.rotation,
                )
                try:
                    self._spawn(argv, backend)
                    return backend
                except (OSError, subprocess.SubprocessError) as exc:
                    log.warning("decoder backend %s failed to spawn (%s)", backend, exc)
                    if self._backend_index == len(self._backends) - 1:
                        raise
                    self._backend_index += 1

    def _spawn(self, argv: list[str], backend: str) -> None:
        log.info("ffmpeg backend=%s cmd=%s", backend, " ".join(argv))
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
        session = _DecoderSession(proc)
        self.proc = proc
        self._session = session
        self.backend = backend
        with self._lock:
            self._slot = None
        self._reader = threading.Thread(
            target=self._read_loop, args=(session,), name="ffmpeg-stdout", daemon=True,
        )
        self._writer = threading.Thread(
            target=self._write_loop, args=(session,), name="ffmpeg-stdin", daemon=True,
        )
        self._reader.start()
        self._writer.start()

    @staticmethod
    def _read_exact(stdout: BinaryIO, n: int, stop: threading.Event) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n and not stop.is_set():
            try:
                chunk = stdout.read(n - len(buf))
            except (OSError, ValueError):
                return None
            if not chunk:
                return None
            buf += chunk
        return bytes(buf) if len(buf) == n else None

    def _request_keyframe(self) -> None:
        if self.on_need_keyframe is not None:
            try:
                self.on_need_keyframe()
            except Exception:
                log.exception("on_need_keyframe handler raised")

    def _mark_dead(self, session: _DecoderSession) -> None:
        with session.condition:
            if session.stop.is_set() or session.dead.is_set():
                return
            session.dead.set()
            session.pending.clear()
            session.queued_bytes = 0
            session.condition.notify_all()
        log.warning("ffmpeg stopped (rc=%s); requesting keyframe + re-init", session.proc.poll())
        self._request_keyframe()

    def _read_loop(self, session: _DecoderSession) -> None:
        stdout = session.proc.stdout
        assert stdout is not None
        while not session.stop.is_set():
            frame = self._read_exact(stdout, self.frame_len, session.stop)
            if frame is None:
                break
            with self._lock:
                if self._slot is not None:
                    self.stats.dropped_stale += 1
                self._slot = frame
                self._slot_seq += 1
                self.stats.decoded += 1
        self._mark_dead(session)

    def _write_loop(self, session: _DecoderSession) -> None:
        stdin = session.proc.stdin
        assert stdin is not None
        try:
            while not session.stop.is_set():
                with session.condition:
                    while not session.pending and not session.stop.is_set() and not session.dead.is_set():
                        session.condition.wait()
                    if session.stop.is_set() or session.dead.is_set():
                        return
                    payload = session.pending.popleft()
                    session.queued_bytes -= len(payload)
                # FileIO.write may write fewer bytes than requested. Finish
                # this entire access unit before starting another one, even
                # if the queue overflows while this write is in progress.
                remaining = memoryview(payload)
                while remaining and not session.stop.is_set():
                    written = stdin.write(remaining)
                    if written is None or written <= 0:
                        raise BrokenPipeError("ffmpeg stdin made no progress")
                    remaining = remaining[written:]
                if not remaining:
                    self.stats.fed += 1
        except (OSError, ValueError):
            self._mark_dead(session)

    def feed(self, payload: bytes, is_keyframe: bool = False) -> bool:
        """Queue an access unit without pipe I/O; return whether accepted.

        Queue overflow drops whole pending units, then waits for a keyframe
        so the decoder never consumes interframes with missing references.
        One in-flight unit plus the bounded queue is retained in memory.
        """
        session = self._session
        if session is None or not payload:
            return False
        request_keyframe = False
        with session.condition:
            if session.stop.is_set() or session.dead.is_set():
                return False
            overflow = (
                len(session.pending) >= self.max_queue_frames
                or session.queued_bytes + len(payload) > self.max_queue_bytes
            )
            if overflow:
                self.stats.dropped_input += len(session.pending)
                session.pending.clear()
                session.queued_bytes = 0
                request_keyframe = not session.waiting_keyframe
                session.waiting_keyframe = True
            accepted = (
                len(payload) <= self.max_queue_bytes
                and (is_keyframe or not session.waiting_keyframe)
            )
            if accepted:
                session.waiting_keyframe = False
                session.pending.append(payload)
                session.queued_bytes += len(payload)
                session.condition.notify()
            else:
                self.stats.dropped_input += 1
        if request_keyframe:
            self._request_keyframe()
        return accepted

    def is_dead(self) -> bool:
        session = self._session
        return session is not None and (
            session.dead.is_set() or session.proc.poll() is not None
        )

    def read_newest(self) -> Optional[bytes]:
        """Take the newest decoded frame, or None when no fresh frame."""
        with self._lock:
            frame, self._slot = self._slot, None
            return frame

    def peek_seq(self) -> int:
        with self._lock:
            return self._slot_seq

    def close(self) -> None:
        """Detach ownership, terminate ffmpeg and stop both pipe workers."""
        with self._lifecycle_lock:
            self._close_locked()

    def _close_locked(self) -> None:
        session, self._session = self._session, None
        self.proc = None
        if session is None:
            return
        session.stop.set()
        with session.condition:
            session.pending.clear()
            session.queued_bytes = 0
            session.condition.notify_all()
        # Kill the pipe consumer before closing stdin: the writer may be
        # blocked inside write(), and must be released before joining it.
        proc = session.proc
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
                proc.wait(timeout=0.5)
            except (subprocess.TimeoutExpired, OSError):
                log.warning("ffmpeg did not exit during cleanup")
        for worker in (self._writer, self._reader):
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=0.5)
        self._writer = self._reader = None
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
        with self._lock:
            self._slot = None
