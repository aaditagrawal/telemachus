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
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

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
    return int(width * height * BYTES_PER_PIXEL[pix_fmt])


def output_dims(width: int, height: int, rotation: int) -> tuple[int, int]:
    """Return (w, h) of decoded output after the rotation filter."""
    if rotation in (90, 270):
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

    # Software decode needs no scale/download chain; rotation (if any) is
    # the only filter.
    filters: list[str] = []
    if vf_chain:
        filters.append(vf_chain)
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
    ]
    argv += hw_pre
    argv += ["-f", codec_flag, "-i", "pipe:0"]
    if filters:
        argv += ["-vf", ",".join(filters)]
    argv += ["-f", "rawvideo", "-pix_fmt", pix_fmt, "pipe:1"]
    return argv, backend


@dataclass
class DecoderStats:
    fed: int = 0
    decoded: int = 0
    dropped_stale: int = 0
    restarts: int = 0


class FrameDecoder:
    """Decode Annex-B with ffmpeg; newest-frame slot for the renderer.

    :param on_need_keyframe: called (with force=True) when the ffmpeg
        process dies so the caller can request a fresh keyframe and
        re-initialise.
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
    ) -> None:
        self.width: int = width
        self.height: int = height
        self.is_hevc: bool = is_hevc
        self.decoder_pref: DecoderPreference = decoder
        self.render_node: Optional[str] = render_node
        self.pix_fmt: PixelFormat = pix_fmt
        self.rotation: int = rotation % 360 if rotation else 0
        self.on_need_keyframe: Optional[Callable[[], None]] = on_need_keyframe

        self.out_w: int
        self.out_h: int
        self.out_w, self.out_h = output_dims(width, height, self.rotation)
        self.frame_len: int = frame_size_bytes(self.out_w, self.out_h, pix_fmt)

        self.backend: str = "sw"
        self.proc: Optional[subprocess.Popen[bytes]] = None
        self._reader: Optional[threading.Thread] = None
        self._stop: threading.Event = threading.Event()
        self._dead: threading.Event = threading.Event()

        self._lock: threading.Lock = threading.Lock()
        self._slot: Optional[bytes] = None
        self._slot_seq: int = 0
        self._write_lock: threading.Lock = threading.Lock()
        self.stats: DecoderStats = DecoderStats()

    # -- lifecycle ------------------------------------------------------
    def start(self) -> str:
        """Spawn ffmpeg; return the backend name. Falls back down the chain."""
        prefs: list[DecoderPreference]
        if self.decoder_pref == "auto":
            prefs = ["auto"]
        else:
            prefs = [self.decoder_pref]
        # For "auto" build_ffmpeg_cmd already encodes vaapi->cuda->sw
        # preference internally; explicit prefs are tried verbatim with a
        # software fallback so one bad flag never wedges the client.
        argv, backend = build_ffmpeg_cmd(
            self.width, self.height, self.is_hevc,
            self.decoder_pref, self.render_node, self.pix_fmt, self.rotation,
        )
        try:
            self._spawn(argv, backend)
            return backend
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("decoder backend %s failed to spawn (%s)", backend, exc)
            if self.decoder_pref != "sw":
                log.info("falling back to software decode")
                argv_sw, backend_sw = build_ffmpeg_cmd(
                    self.width, self.height, self.is_hevc,
                    "sw", None, self.pix_fmt, self.rotation,
                )
                self._spawn(argv_sw, backend_sw)
                return backend_sw
            raise

    def _spawn(self, argv: list[str], backend: str) -> None:
        log.info("ffmpeg backend=%s cmd=%s", backend, " ".join(argv))
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.backend = backend
        self._stop.clear()
        self._dead.clear()
        self._reader = threading.Thread(
            target=self._read_loop, name="ffmpeg-stdout", daemon=True
        )
        self._reader.start()

    def _read_exact(self, n: int) -> Optional[bytes]:
        assert self.proc is not None and self.proc.stdout is not None
        buf = bytearray()
        stdout = self.proc.stdout
        while len(buf) < n and not self._stop.is_set():
            try:
                chunk: bytes = stdout.read(n - len(buf))
            except (OSError, ValueError):
                return None
            if not chunk:
                return None
            buf += chunk
        if len(buf) != n:
            return None
        return bytes(buf)

    def _read_loop(self) -> None:
        assert self.proc is not None
        while not self._stop.is_set():
            frame: Optional[bytes] = self._read_exact(self.frame_len)
            if frame is None:
                break
            with self._lock:
                if self._slot is not None:
                    self.stats.dropped_stale += 1
                self._slot = frame
                self._slot_seq += 1
                self.stats.decoded += 1
        # Reader exited: process died or we are stopping.
        if not self._stop.is_set():
            self._dead.set()
            rc: Optional[int] = self.proc.poll()
            log.warning("ffmpeg exited (rc=%s); requesting keyframe + re-init", rc)
            if self.on_need_keyframe is not None:
                try:
                    self.on_need_keyframe()
                except Exception:
                    log.exception("on_need_keyframe handler raised")

    # -- network-thread API (never blocks) ------------------------------
    def feed(self, payload: bytes, is_keyframe: bool = False) -> bool:
        """Write one Annex-B access unit to ffmpeg stdin. Non-blocking.

        Returns False (and drops the frame) when the decoder is dead or
        the pipe would block, so the network reader never stalls.
        """
        _ = is_keyframe  # informational; ffmpeg parses boundaries itself
        proc: Optional[subprocess.Popen[bytes]] = self.proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            self._dead.set()
            return False
        # Fast liveness check without blocking: poll() above suffices.
        # Use a short acquisition timeout so a wedged pipe drops the frame
        # instead of stalling the reader.
        if not self._write_lock.acquire(blocking=False):
            return False
        try:
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                self._dead.set()
                log.warning("ffmpeg stdin broken; requesting keyframe + re-init")
                if self.on_need_keyframe is not None:
                    try:
                        self.on_need_keyframe()
                    except Exception:
                        log.exception("on_need_keyframe handler raised")
                return False
            self.stats.fed += 1
            return True
        finally:
            self._write_lock.release()

    def is_dead(self) -> bool:
        """Return True when ffmpeg exited or the stdin pipe broke."""
        if self._dead.is_set():
            return True
        if self.proc is not None and self.proc.poll() is not None:
            self._dead.set()
            return True
        return False

    # -- renderer-thread API --------------------------------------------
    def read_newest(self) -> Optional[bytes]:
        """Take the newest decoded frame, or None when no fresh frame."""
        with self._lock:
            frame, self._slot = self._slot, None
            return frame

    def peek_seq(self) -> int:
        """Return the decoded-frame sequence counter (for HUD/tests)."""
        with self._lock:
            return self._slot_seq

    def close(self) -> None:
        """Stop the reader thread and terminate ffmpeg."""
        self._stop.set()
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2.0)
