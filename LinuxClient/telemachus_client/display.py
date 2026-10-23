"""SDL2 display for the Telemachus Linux client (pysdl2, imported lazily).

Pure geometry helpers (:func:`content_rect`, :func:`normalize_to_content`)
are import-safe and mirror the Android ``TouchMapper``: the video is
letterboxed into the window preserving aspect ratio, and input coordinates
are normalised through that same content rect, clamped to 0..1.

The :class:`SdlDisplay` class imports ``sdl2`` only when opened, so
``--help`` and unit tests work on machines without SDL/pysdl2.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger("telemachus.display")


# -- pure geometry (no SDL dependency) -----------------------------------
def inverse_rotation(x: float, y: float, rotation: int) -> tuple[float, float]:
    """Map normalized coordinates in the rotated picture back to the Mac."""
    rotation %= 360
    if rotation == 90:
        return y, 1.0 - x
    if rotation == 180:
        return 1.0 - x, 1.0 - y
    if rotation == 270:
        return 1.0 - y, x
    return x, y


def content_rect(
    view_w: int, view_h: int, video_w: int, video_h: int
) -> tuple[float, float, float, float]:
    """Return (x, y, w, h) of the letterboxed video inside the window.

    Mirrors Android TouchMapper: scale to fit, centre, black bars elsewhere.
    Returns zeros for degenerate inputs.
    """
    if view_w <= 0 or view_h <= 0 or video_w <= 0 or video_h <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    surface_aspect: float = view_w / view_h
    video_aspect: float = video_w / video_h
    if surface_aspect > video_aspect:
        ch: float = float(view_h)
        cw: float = ch * video_aspect
        ox: float = (view_w - cw) / 2.0
        oy: float = 0.0
    else:
        cw = float(view_w)
        ch = cw / video_aspect
        ox = 0.0
        oy = (view_h - ch) / 2.0
    return (ox, oy, cw, ch)


def normalize_to_content(
    x: float,
    y: float,
    view_w: int,
    view_h: int,
    video_w: int,
    video_h: int,
) -> tuple[float, float]:
    """Map window pixels to 0..1 video coordinates through the content rect."""
    ox, oy, cw, ch = content_rect(view_w, view_h, video_w, video_h)
    if cw <= 0 or ch <= 0:
        return (0.0, 0.0)
    nx: float = (x - ox) / cw
    ny: float = (y - oy) / ch
    return (min(1.0, max(0.0, nx)), min(1.0, max(0.0, ny)))


@dataclass(frozen=True)
class DisplayMode:
    index: int
    name: str
    width: int
    height: int
    refresh_hz: int


def match_display(
    spec: Optional[str], modes: list[DisplayMode]
) -> int:
    """Pick a display index from *modes* using the client resolution rules.

    * Integer ``spec`` selects by SDL display index.
    * ``WxH@Hz`` (e.g. ``1920x1080@60``) selects by mode match.
    * Anything else matches case-insensitively against the display name.
    * ``None``/empty defaults to the 1920x1080@60 display when present
      (disambiguates the external HP from the 144 Hz internal panel
      without hardcoding Wayland connector names), else display 0.
    """
    if not modes:
        return 0
    if spec is None or not spec.strip():
        for m in modes:
            if m.width == 1920 and m.height == 1080 and m.refresh_hz == 60:
                return m.index
        return modes[0].index
    s: str = spec.strip()
    if re.fullmatch(r"\d+", s):
        idx: int = int(s)
        if any(m.index == idx for m in modes):
            return idx
        log.warning("display index %d not found; using %d", idx, modes[0].index)
        return modes[0].index
    m_wh: Optional[re.Match[str]] = re.fullmatch(
        r"(\d+)\s*x\s*(\d+)(?:\s*@\s*(\d+))?", s, re.IGNORECASE
    )
    if m_wh:
        w, h = int(m_wh.group(1)), int(m_wh.group(2))
        hz: Optional[int] = int(m_wh.group(3)) if m_wh.group(3) else None
        for m in modes:
            if m.width == w and m.height == h and (hz is None or m.refresh_hz == hz):
                return m.index
        log.warning("no %s display; using %d", s, modes[0].index)
        return modes[0].index
    lowered: str = s.lower()
    for m in modes:
        if lowered in m.name.lower():
            return m.index
    log.warning("no display matching %r; using %d", s, modes[0].index)
    return modes[0].index


# -- SDL window (pysdl2 imported lazily) ----------------------------------
class SdlDisplay:
    """SDL2 window presenting NV12 frames via an SDL YUV texture.

    :param on_touch: called as ``on_touch(x, y, action, count, x2, y2)``
        with normalised 0..1 coordinates (type-2 touch semantics).
    :param display_spec: integer index, name substring, or WxH@Hz.
    :param motion_min_interval_s: throttle for motion events (~120 Hz).
    """

    # SDL mouse button constants (avoid importing sdl2 at module scope).
    _BTN_LEFT: int = 1
    _BTN_RIGHT: int = 3

    def __init__(
        self,
        on_touch: Optional[Callable[[float, float, int, int, float, float], None]] = None,
        display_spec: Optional[str] = None,
        fullscreen: bool = True,
        hud_enabled: bool = False,
        motion_min_interval_s: float = 0.008,
    ) -> None:
        self.on_touch: Optional[Callable[[float, float, int, int, float, float], None]] = on_touch
        self.display_spec: Optional[str] = display_spec
        self.fullscreen: bool = fullscreen
        self.hud_enabled: bool = hud_enabled
        self.motion_min_interval_s: float = motion_min_interval_s

        self._sdl: Any = None
        self._window: Any = None
        self._renderer: Any = None
        self._texture: Any = None
        self.video_w: int = 0
        self.video_h: int = 0
        self.win_w: int = 0
        self.win_h: int = 0
        self.display_index: int = 0
        self.should_quit: bool = False
        self.has_focus: bool = True
        self._last_motion: float = 0.0
        self._left_down: bool = False
        self._gesture_button: Optional[int] = None
        self._gesture_count: int = 1
        self._last_touch = (0.0, 0.0, 1, 0.0, 0.0)
        self._fps: float = 0.0
        self._mbps: float = 0.0
        self._rtt_ms: float = 0.0
        self._hud_last_log: float = 0.0

    # -- open / close ----------------------------------------------------
    def open(self, video_w: int, video_h: int) -> None:
        """Create the SDL window/renderer/texture for a *video_w* x *video_h* stream."""
        import sdl2  # noqa: PLC0415  (lazy so --help works without pysdl2)
        import sdl2.ext  # noqa: PLC0415

        _ = sdl2.ext  # keep linters quiet; ext initialises subsystems
        self._sdl = sdl2
        self.should_quit = False
        self.has_focus = True
        self.video_w, self.video_h = video_w, video_h
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
            raise RuntimeError(
                f"SDL_Init failed: {sdl2.SDL_GetError().decode(errors='replace')}"
            )
        modes: list[DisplayMode] = self._enumerate_modes()
        self.display_index = match_display(self.display_spec, modes)
        flags: int = sdl2.SDL_WINDOW_ALLOW_HIGHDPI
        if self.fullscreen:
            flags |= sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP
        else:
            flags |= sdl2.SDL_WINDOW_RESIZABLE
        xpos: int = sdl2.SDL_WINDOWPOS_CENTERED_DISPLAY(self.display_index)
        # Window sized to the stream; FULLSCREEN_DESKTOP scales to the monitor.
        self._window = sdl2.SDL_CreateWindow(
            b"Telemachus",
            xpos, xpos,
            video_w, video_h,
            flags,
        )
        if not self._window:
            raise RuntimeError(
                f"SDL_CreateWindow failed: {sdl2.SDL_GetError().decode(errors='replace')}"
            )
        self._renderer = sdl2.SDL_CreateRenderer(self._window, -1, sdl2.SDL_RENDERER_ACCELERATED)
        if not self._renderer:
            log.info("accelerated SDL renderer unavailable; using software rendering")
            self._renderer = sdl2.SDL_CreateRenderer(self._window, -1, sdl2.SDL_RENDERER_SOFTWARE)
        if not self._renderer:
            raise RuntimeError(
                f"SDL_CreateRenderer failed: {sdl2.SDL_GetError().decode(errors='replace')}"
            )
        # NV12 texture: one Y plane + interleaved UV. SDL_PIXELFORMAT_NV12
        # with SDL_TEXTUREACCESS_STREAMING keeps upload near ~186 MB/s at
        # 1080p60 (vs ~497 MB/s for BGRA).
        self._texture = sdl2.SDL_CreateTexture(
            self._renderer, sdl2.SDL_PIXELFORMAT_NV12,
            sdl2.SDL_TEXTUREACCESS_STREAMING, video_w, video_h,
        )
        if not self._texture:
            raise RuntimeError(
                f"SDL_CreateTexture failed: {sdl2.SDL_GetError().decode(errors='replace')}"
            )
        self._sync_window_size()
        log.info(
            "SDL window on display %d (%s), window=%dx%d video=%dx%d %s",
            self.display_index,
            modes[self.display_index].name if self.display_index < len(modes) else "?",
            self.win_w, self.win_h, video_w, video_h,
            "fullscreen" if self.fullscreen else "windowed",
        )

    def _enumerate_modes(self) -> list[DisplayMode]:
        sdl2: Any = self._sdl
        modes: list[DisplayMode] = []
        n: int = sdl2.SDL_GetNumVideoDisplays()
        for i in range(max(1, n)):
            try:
                raw_name = sdl2.SDL_GetDisplayName(i)
                name: str = raw_name.decode(errors="replace") if raw_name else f"display{i}"
            except Exception:
                name = f"display{i}"
            w, h, hz = 0, 0, 0
            try:
                mode = sdl2.SDL_DisplayMode()
                if sdl2.SDL_GetCurrentDisplayMode(i, mode) == 0:
                    w, h, hz = int(mode.w), int(mode.h), int(mode.refresh_rate)
            except Exception:
                pass
            modes.append(DisplayMode(index=i, name=name, width=w, height=h, refresh_hz=hz))
        if not modes:
            modes.append(DisplayMode(index=0, name="display0", width=0, height=0, refresh_hz=0))
        return modes

    def _sync_window_size(self) -> None:
        sdl2: Any = self._sdl
        w = __import__("ctypes").c_int()
        h = __import__("ctypes").c_int()
        sdl2.SDL_GetWindowSize(self._window, w, h)
        self.win_w, self.win_h = int(w.value), int(h.value)

    # -- presentation ----------------------------------------------------
    def present(self, frame_nv12: bytes) -> None:
        """Upload one NV12 frame and render it letterboxed."""
        sdl2: Any = self._sdl
        expect: int = self.video_w * self.video_h * 3 // 2
        if len(frame_nv12) != expect:
            log.warning(
                "dropping frame with wrong size %d (expected %d)",
                len(frame_nv12), expect,
            )
            return
        sdl2.SDL_UpdateTexture(self._texture, None, frame_nv12, self.video_w)
        sdl2.SDL_SetRenderDrawColor(self._renderer, 0, 0, 0, 255)
        sdl2.SDL_RenderClear(self._renderer)
        ox, oy, cw, ch = content_rect(self.win_w, self.win_h, self.video_w, self.video_h)
        import ctypes

        dst = sdl2.SDL_Rect(int(ox), int(oy), int(cw), int(ch))
        sdl2.SDL_RenderCopy(self._renderer, self._texture, None, dst)
        sdl2.SDL_RenderPresent(self._renderer)
        void_p = ctypes.c_void_p
        _ = void_p  # readability anchor; dst must stay alive through RenderCopy

    def set_stats(self, fps: float, mbps: float, rtt_ms: float) -> None:
        """Update HUD telemetry (window title + ~1 Hz log when enabled)."""
        self._fps, self._mbps, self._rtt_ms = fps, mbps, rtt_ms
        if not self.hud_enabled or self._sdl is None or self._window is None:
            return
        title: bytes = (
            f"Telemachus  {fps:5.1f} fps  {mbps:5.1f} Mb/s  {rtt_ms:5.1f} ms RTT"
        ).encode()
        self._sdl.SDL_SetWindowTitle(self._window, title)
        now: float = time.monotonic()
        if now - self._hud_last_log >= 1.0:
            self._hud_last_log = now
            log.info("hud fps=%.1f mbps=%.1f rtt=%.1fms", fps, mbps, rtt_ms)

    # -- input ------------------------------------------------------------
    def poll(self) -> None:
        """Pump SDL events; forward workstation mouse as type-2 touch.

        Only forwards while the window has focus. Left button: DOWN->0,
        MOTION->1, UP->2. Shift+left-drag or right-drag synthesises
        count=2 with x2/y2. Motion throttled to ~120 Hz.
        """
        sdl2: Any = self._sdl
        ev = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ev) != 0:
            etype: int = ev.type
            if etype == sdl2.SDL_QUIT:
                self._release_touch()
                self.should_quit = True
            elif etype == sdl2.SDL_WINDOWEVENT:
                wev = ev.window
                if wev.event == sdl2.SDL_WINDOWEVENT_CLOSE:
                    self._release_touch()
                    self.should_quit = True
                elif wev.event in (sdl2.SDL_WINDOWEVENT_FOCUS_GAINED,):
                    self.has_focus = True
                elif wev.event in (sdl2.SDL_WINDOWEVENT_FOCUS_LOST,):
                    self.has_focus = False
                    self._release_touch()
                elif wev.event in (
                    sdl2.SDL_WINDOWEVENT_RESIZED,
                    sdl2.SDL_WINDOWEVENT_SIZE_CHANGED,
                ):
                    self._release_touch()
                    self._sync_window_size()
            elif etype == sdl2.SDL_KEYDOWN:
                keysym = ev.key.keysym.sym
                # F1 toggles the HUD overlay.
                if keysym == sdl2.SDLK_F1:
                    self.hud_enabled = not self.hud_enabled
                    log.info("HUD %s", "on" if self.hud_enabled else "off")
                elif keysym == sdl2.SDLK_ESCAPE and not self.fullscreen:
                    self._release_touch()
                    self.should_quit = True
            elif etype in (sdl2.SDL_MOUSEBUTTONDOWN, sdl2.SDL_MOUSEBUTTONUP):
                if not self.has_focus or self.on_touch is None:
                    continue
                btn = ev.button
                pressed: bool = etype == sdl2.SDL_MOUSEBUTTONDOWN
                if btn.button not in (self._BTN_LEFT, self._BTN_RIGHT):
                    continue
                if pressed:
                    if self._left_down:
                        continue
                    self._gesture_button = btn.button
                    self._left_down = True
                    self._last_motion = 0.0
                    self._gesture_count = 2 if btn.button == self._BTN_RIGHT or self._shift_held() else 1
                elif not self._left_down or btn.button != self._gesture_button:
                    continue
                x, y = self._normalise(int(btn.x), int(btn.y))
                count, x2, y2 = self._pinch_pair(int(btn.x), int(btn.y), x, y,
                                                two=self._gesture_count == 2)
                self._last_touch = (x, y, count, x2, y2)
                if pressed:
                    self.on_touch(x, y, 0, count, x2, y2)
                else:
                    self._release_touch()
            elif etype == sdl2.SDL_MOUSEMOTION:
                if not self.has_focus or self.on_touch is None or not self._left_down:
                    continue
                now = time.monotonic()
                if now - self._last_motion < self.motion_min_interval_s:
                    continue
                self._last_motion = now
                mot = ev.motion
                x, y = self._normalise(int(mot.x), int(mot.y))
                count, x2, y2 = self._pinch_pair(int(mot.x), int(mot.y), x, y,
                                                two=self._gesture_count == 2)
                self._last_touch = (x, y, count, x2, y2)
                self.on_touch(x, y, 1, count, x2, y2)

    def _release_touch(self) -> None:
        """End an active gesture using its last coordinates and pointer count."""
        if not self._left_down:
            return
        self._left_down = False
        self._gesture_button = None
        x, y, count, x2, y2 = self._last_touch
        if self.on_touch is not None:
            self.on_touch(x, y, 2, count, x2, y2)

    def _shift_held(self) -> bool:
        sdl2: Any = self._sdl
        mods: int = int(sdl2.SDL_GetModState())
        return bool(mods & (sdl2.KMOD_LSHIFT | sdl2.KMOD_RSHIFT))

    def _right_held(self) -> bool:
        sdl2: Any = self._sdl
        import ctypes  # noqa: PLC0415

        x = ctypes.c_int()
        y = ctypes.c_int()
        buttons: int = int(sdl2.SDL_GetMouseState(x, y))
        return bool(buttons & sdl2.SDL_BUTTON(self._BTN_RIGHT))

    def _pinch_pair(
        self, px: int, py: int, x: float, y: float, two: Optional[bool] = None
    ) -> tuple[int, float, float]:
        """Return (count, x2, y2); two-finger when shift/right is held."""
        if two is None:
            two = self._shift_held() or self._right_held()
        if not two:
            return (1, 0.0, 0.0)
        # Mirror the point through the content-rect centre so a
        # shift-drag reads as a pinch on the Mac.
        ox, oy, cw, ch = content_rect(
            self.win_w, self.win_h, self.video_w, self.video_h
        )
        if cw <= 0 or ch <= 0:
            return (2, x, y)
        cx: float = ox + cw / 2.0
        cy: float = oy + ch / 2.0
        mx: float = 2.0 * cx - px
        my: float = 2.0 * cy - py
        x2, y2 = normalize_to_content(
            mx, my, self.win_w, self.win_h, self.video_w, self.video_h
        )
        return (2, x2, y2)

    def _normalise(self, px: int, py: int) -> tuple[float, float]:
        return normalize_to_content(
            float(px), float(py), self.win_w, self.win_h, self.video_w, self.video_h
        )

    def close(self) -> None:
        """Destroy the texture/renderer/window and quit SDL video."""
        self._release_touch()
        sdl2: Any = self._sdl
        try:
            if self._texture is not None:
                sdl2.SDL_DestroyTexture(self._texture)
            if self._renderer is not None:
                sdl2.SDL_DestroyRenderer(self._renderer)
            if self._window is not None:
                sdl2.SDL_DestroyWindow(self._window)
        except Exception:
            pass
        finally:
            self._texture = None
            self._renderer = None
            self._window = None
        try:
            if sdl2 is not None:
                sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_VIDEO)
        except Exception:
            pass
