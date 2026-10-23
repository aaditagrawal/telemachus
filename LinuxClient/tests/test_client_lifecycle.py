"""Client shutdown, decoder ownership, rotation and focus-release seams."""

import argparse
import socket
import threading
import time

import pytest

from telemachus_client import __main__ as cli
from telemachus_client import decode, display, netclient
from telemachus_client.config import ClientConfig
from telemachus_client.protocol import ProtocolError


def test_close_interrupts_handshake_and_cannot_reconnect():
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(2)
    connected = threading.Event()
    server_stop = threading.Event()
    errors = []

    def server():
        with listener:
            peer, _ = listener.accept()
            with peer:
                assert peer.recv(1024).startswith(b"SSWA")
                connected.set()
                server_stop.wait(3)

    server_thread = threading.Thread(target=server)
    server_thread.start()
    client = netclient.StreamClient(ClientConfig(
        host="127.0.0.1", port=listener.getsockname()[1], token=bytes(32), name="test",
    ))

    def connect():
        try:
            client.connect()
        except ProtocolError as exc:
            errors.append(exc)

    worker = threading.Thread(target=connect)
    worker.start()
    try:
        assert connected.wait(2)
        started = time.monotonic()
        client.close()
        client.close()
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert time.monotonic() - started < 1
        assert errors
        assert not client.connected
        assert client._sock is None
        with pytest.raises(ProtocolError, match="closed"):
            client.connect()
    finally:
        client.close()
        server_stop.set()
        server_thread.join(timeout=3)
        worker.join(timeout=3)
    assert not server_thread.is_alive()


def test_close_before_supervisor_start_is_not_cleared(monkeypatch):
    client = netclient.StreamClient(ClientConfig(host="127.0.0.1", port=54321, token=b"", name="test"))
    calls = []
    monkeypatch.setattr(client, "connect", lambda: calls.append(True))
    client.close()
    client.run_supervisor()
    assert not calls


@pytest.mark.parametrize("response", [b"WRNG\x00", b"SSWR\xff", b"", None])
def test_failed_handshake_always_closes_socket(monkeypatch, response):
    class BadHandshakeSocket:
        def __init__(self):
            self.closed = False

        def setsockopt(self, *args):
            pass

        def settimeout(self, *args):
            pass

        def connect(self, *args):
            pass

        def sendall(self, *args):
            pass

        def recv(self, count):
            if response is None:
                raise socket.timeout()
            return response

        def close(self):
            self.closed = True

    sock = BadHandshakeSocket()
    monkeypatch.setattr(netclient.socket, "socket", lambda *args: sock)
    client = netclient.StreamClient(ClientConfig(host="test", port=54321, token=bytes(32), name="test"))
    with pytest.raises(ProtocolError):
        client.connect()
    assert sock.closed
    assert client._sock is None
    assert not client.connected


@pytest.mark.parametrize("rotation,first,second", [
    (0, (0.25, 0.25), (0.75, 0.75)),
    (90, (0.25, 0.75), (0.75, 0.25)),
    (180, (0.75, 0.75), (0.25, 0.25)),
    (270, (0.75, 0.25), (0.25, 0.75)),
])
def test_render_thread_owns_decoder_lifecycle_and_allows_unfocused_up(monkeypatch, rotation, first, second):
    main_thread = threading.get_ident()
    decoders = []
    clients = []
    windows = []
    lifecycle = []
    callback_durations = []
    errors = []

    class Decoder:
        def __init__(self, width, height, is_hevc, rotation=0, **kwargs):
            self.width, self.height = width, height
            self.is_hevc, self.rotation = is_hevc, rotation
            self.out_w, self.out_h = decode.output_dims(width, height, rotation)
            self.proc = None
            self.dead = False
            self.closing = False
            self.starts = self.feeds = 0
            self.frame = None
            decoders.append(self)

        def start(self):
            lifecycle.append(("start", threading.get_ident()))
            self.proc = object()
            self.starts += 1
            self.dead = False
            return "sw"

        def feed(self, payload, keyframe):
            if self.closing:
                errors.append("feed raced close")
            self.feeds += 1
            self.frame = bytes(self.out_w * self.out_h * 3 // 2)
            return True

        def is_dead(self):
            return self.dead

        def read_newest(self):
            frame, self.frame = self.frame, None
            return frame

        def close(self):
            lifecycle.append(("close", threading.get_ident()))
            self.closing = True
            # Network frame callbacks must keep running during teardown.
            time.sleep(0.03)
            self.proc = None

    class Window:
        def __init__(self, **kwargs):
            self.has_focus = True
            self.should_quit = False
            self.on_touch = None
            self.dims = None
            self.opened = []
            self.presented = []
            self.sent_input = False
            windows.append(self)

        def open(self, width, height):
            self.dims = (width, height)
            self.opened.append(self.dims)

        def poll(self):
            if not self.sent_input:
                self.sent_input = True
                self.on_touch(0.25, 0.25, 0, 2, 0.75, 0.75)
                self.has_focus = False
                self.on_touch(0.5, 0.5, 1, 2, 0.5, 0.5)
                self.on_touch(0.25, 0.25, 2, 2, 0.75, 0.75)
            if len(decoders) == 2 and decoders[-1].feeds >= 2:
                self.should_quit = True

        def present(self, frame):
            assert len(frame) == self.dims[0] * self.dims[1] * 3 // 2
            self.presented.append(frame)
            # The callback must not wait for renderer work either.
            time.sleep(0.03)

        def set_stats(self, *args):
            pass

        def close(self):
            pass

    class Client:
        def __init__(self, cfg, **callbacks):
            self.callbacks = callbacks
            self.done = threading.Event()
            self.touches = []
            self.keyframes = 0
            clients.append(self)

        def send_capabilities(self, **kwargs):
            pass

        def run_supervisor(self):
            cb = self.callbacks
            cb["on_state"](True)
            cb["on_codec"](False)
            cb["on_display"](64, 48, rotation)
            failed = resized = False
            deadline = time.monotonic() + 3
            while not self.done.is_set() and time.monotonic() < deadline:
                started = time.monotonic()
                cb["on_frame"](b"unit", True, 0)
                callback_durations.append(time.monotonic() - started)
                if decoders and decoders[0].feeds >= 2 and not failed:
                    failed = True
                    decoders[0].dead = True
                elif decoders and decoders[0].starts == 2 and not resized:
                    resized = True
                    cb["on_display"](80, 60, (rotation + 180) % 360)
                time.sleep(0.001)
            if not self.done.is_set():
                errors.append("client loop timed out")
                cb["on_shutdown"]()
            cb["on_state"](False)

        def request_keyframe(self, **kwargs):
            self.keyframes += 1

        def send_touch(self, *args, **kwargs):
            self.touches.append((args, kwargs))

        def send_ping(self):
            pass

        def close(self):
            self.done.set()

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, *args):
            pass

        def connect(self, *args):
            pass

    monkeypatch.setattr(decode, "FrameDecoder", Decoder)
    monkeypatch.setattr(display, "SdlDisplay", Window)
    monkeypatch.setattr(netclient, "StreamClient", Client)
    monkeypatch.setattr(cli.socket, "socket", lambda *args: Probe())
    cfg = cli.ResolvedConfig("test", 54321, bytes(32), "test", "auto", None, False, True)
    assert cli.run_client(argparse.Namespace(hud=False, selftest=False), cfg) == 0
    assert not errors
    assert len(decoders) == 2
    assert decoders[0].starts == 2
    assert windows[0].opened == [
        decode.output_dims(64, 48, rotation),
        decode.output_dims(80, 60, (rotation + 180) % 360),
    ]
    assert lifecycle and all(owner == main_thread for _, owner in lifecycle)
    assert all(decoder.proc is None for decoder in decoders)
    assert max(callback_durations) < 0.02
    assert [args[2] for args, _ in clients[0].touches] == [0, 2]
    assert [kwargs["count"] for _, kwargs in clients[0].touches] == [2, 2]
    for args, kwargs in clients[0].touches:
        assert args[:2] == first
        assert (kwargs["x2"], kwargs["y2"]) == second
    assert clients[0].keyframes >= 2
