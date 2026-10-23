"""SDL input regressions run against its headless offscreen driver."""

import ctypes

import pytest

from telemachus_client.display import SdlDisplay


@pytest.fixture
def display(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "offscreen")
    sdl = pytest.importorskip("sdl2")
    touches = []
    disp = SdlDisplay(on_touch=lambda *args: touches.append(args), fullscreen=False,
                      motion_min_interval_s=0)
    try:
        disp.open(64, 48)
    except RuntimeError as exc:
        disp.close()
        pytest.skip(f"SDL offscreen driver unavailable: {exc}")
    sdl.SDL_FlushEvents(sdl.SDL_FIRSTEVENT, sdl.SDL_LASTEVENT)
    yield disp, sdl, touches
    disp.close()
    sdl.SDL_SetModState(0)


def push(disp, sdl, kind, **fields):
    event = sdl.SDL_Event()
    event.type = kind
    if kind in (sdl.SDL_MOUSEBUTTONDOWN, sdl.SDL_MOUSEBUTTONUP):
        event.button.button = fields.get("button", sdl.SDL_BUTTON_LEFT)
        event.button.x, event.button.y = 16, 12
    elif kind == sdl.SDL_MOUSEMOTION:
        event.motion.x, event.motion.y = 32, 24
    elif kind == sdl.SDL_WINDOWEVENT:
        event.window.event = fields["window_event"]
    elif kind == sdl.SDL_KEYDOWN:
        event.key.keysym.sym = fields["key"]
    assert sdl.SDL_PushEvent(ctypes.byref(event)) == 1
    disp.poll()


@pytest.mark.parametrize("ending", ["focus", "resize", "size_changed", "window_close", "quit", "escape", "close"])
def test_interrupted_gesture_sends_exactly_one_up(display, ending):
    disp, sdl, touches = display
    push(disp, sdl, sdl.SDL_MOUSEBUTTONDOWN)
    push(disp, sdl, sdl.SDL_MOUSEMOTION)
    assert [t[2] for t in touches] == [0, 1]
    if ending == "close":
        disp.close()
    elif ending == "quit":
        push(disp, sdl, sdl.SDL_QUIT)
    elif ending == "escape":
        push(disp, sdl, sdl.SDL_KEYDOWN, key=sdl.SDLK_ESCAPE)
    else:
        window_event = {
            "focus": sdl.SDL_WINDOWEVENT_FOCUS_LOST,
            "resize": sdl.SDL_WINDOWEVENT_RESIZED,
            "size_changed": sdl.SDL_WINDOWEVENT_SIZE_CHANGED,
            "window_close": sdl.SDL_WINDOWEVENT_CLOSE,
        }[ending]
        push(disp, sdl, sdl.SDL_WINDOWEVENT, window_event=window_event)
    assert [t[2] for t in touches] == [0, 1, 2]
    assert touches[-1][:2] == touches[-2][:2]
    assert not disp._left_down
    disp.close()
    assert len(touches) == 3


@pytest.mark.parametrize("button,shift", [(1, True), (3, False)])
def test_two_pointer_count_survives_modifier_release_and_focus_loss(display, button, shift):
    disp, sdl, touches = display
    sdl.SDL_SetModState(sdl.KMOD_LSHIFT if shift else 0)
    push(disp, sdl, sdl.SDL_MOUSEBUTTONDOWN, button=button)
    sdl.SDL_SetModState(0)
    push(disp, sdl, sdl.SDL_MOUSEMOTION)
    push(disp, sdl, sdl.SDL_WINDOWEVENT, window_event=sdl.SDL_WINDOWEVENT_FOCUS_LOST)
    assert [t[2] for t in touches] == [0, 1, 2]
    assert [t[3] for t in touches] == [2, 2, 2]
    assert touches[-1][:2] + touches[-1][3:] == touches[-2][:2] + touches[-2][3:]
    # A physical release after focus returns must not send a second UP.
    push(disp, sdl, sdl.SDL_WINDOWEVENT, window_event=sdl.SDL_WINDOWEVENT_FOCUS_GAINED)
    push(disp, sdl, sdl.SDL_MOUSEBUTTONUP, button=button)
    assert len(touches) == 3


def test_right_button_up_keeps_two_pointer_gesture(display):
    disp, sdl, touches = display
    push(disp, sdl, sdl.SDL_MOUSEBUTTONDOWN, button=3)
    push(disp, sdl, sdl.SDL_MOUSEBUTTONUP, button=3)
    assert [t[2] for t in touches] == [0, 2]
    assert [t[3] for t in touches] == [2, 2]


def test_unrelated_button_release_does_not_end_drag(display):
    disp, sdl, touches = display
    push(disp, sdl, sdl.SDL_MOUSEBUTTONDOWN)
    push(disp, sdl, sdl.SDL_MOUSEBUTTONUP, button=3)
    push(disp, sdl, sdl.SDL_MOUSEMOTION)
    push(disp, sdl, sdl.SDL_MOUSEBUTTONUP)
    assert [t[2] for t in touches] == [0, 1, 2]
