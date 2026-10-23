# Telemachus Linux client

The Linux client for Telemachus. The Mac remains the host; this
receiver connects to it over the LAN, decodes the HEVC/H.264 stream with a
system `ffmpeg` subprocess, and presents it in an SDL2 window.

Requires Python 3.10 or newer, SDL2, and ffmpeg on a graphical Linux session.
The default runtime is pinned to Python 3.12 for Ubuntu 24.04 and derivatives.

## Put the Mac in Wireless mode first

USB mode only accepts loopback connections (it is the `adb reverse` path for
Android). A Linux workstation on the LAN is a non-loopback client, so it is
rejected unless the Mac host is in **Wireless** mode with a matching token.

Mac-side steps:

1. Open Telemachus on the Mac.
2. Switch the transport/mode selector to **Wireless** (not USB).
3. Start streaming. The Mac shows a QR code containing
   `telemachus://HOST:PORT?t=TOKEN&name=NAME`.
4. On this machine, pair with `--qr` (below). Anyone holding that URI holds
   the credential until the token is reset on the Mac.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first,
then run these commands from `LinuxClient/`.

```bash
sudo apt update
sudo apt install ffmpeg libsdl2-2.0-0
uv sync --frozen  # pysdl2 and the development tools, in an isolated environment
```

VAAPI needs access to the render nodes:

```bash
ls -l /dev/dri/renderD*
sudo usermod -aG video,render "$USER"
# log out and back in, then verify:
groups | tr ' ' '\n' | grep -E '^(video|render)$'
```

`ffmpeg` comes from apt. If pysdl2 cannot find `libSDL2.so`,
install `libsdl2-2.0-0` (above); `pysdl2-dll` is only relevant outside Linux
distro packaging.

### Per-user install (no root)

```bash
./scripts/install-linux.sh
```

Copies the client to `~/.local/share/telemachus` and symlinks
`telemachus-linux` and `telemachus-scout` into `~/.local/bin` (the path the
systemd unit's `ExecStart` uses). Reinstalling updates the client and its
locked Python dependencies. Enable the service and pair separately.
Add `~/.local/bin` to `PATH` if it is not already there. The installed
launchers reuse the client's isolated environment, including when systemd
starts the receiver.

## Pairing

One-shot pairing from the Mac QR code:

```bash
./telemachus-linux --qr 'telemachus://192.168.1.50:54321?t=...&name=mac'
```

The token is saved to the config file at `~/.config/telemachus/client.conf`
(token file mode `0600`). `--qr` writes host/port/token/name itself, so
pairing is one command, not a flag you have to keep repeating. Later runs
reuse it:

```bash
./telemachus-linux
./telemachus-linux --host 192.168.1.50 --port 54321 --name zorin
```

The write is atomic (temp file + rename) and merges: decoder, display, and
input settings already in the file are preserved. Precedence: CLI flags >
config file > defaults. `--qr` fills any of host/port/token/name the CLI did
not set explicitly.

## Tailscale discovery

When both machines are on the same tailnet, the client can list candidates:

```bash
./telemachus-linux --discover-tailscale
```

It reads `tailscale status --json`, keeps online peers, probes the configured
TCP port (54321 by default) on each with a 1 s timeout in parallel, prints a
numbered list, and waits for a selection. With no `--host` in the CLI or
config and an interactive terminal, the same picker is offered automatically;
pass `--host` to skip it. Scripts, cron, and systemd never get the prompt
because discovery requires a TTY.

Discovery only finds the host; it does not pair. If the config has no token,
the client prints the `--qr` command to run with the pairing URI from the Mac
and exits without generating a token. `--qr` always takes precedence: when a
QR URI is passed, discovery is skipped. If the `tailscale` CLI is missing,
explicit discovery reports it and exits; automatic discovery falls back to
the configured host.

Security note: discovery reads the local daemon's peer list, probes the
Telemachus port, and connects only after you type a selection. It does not
authenticate peers, send the token, or print it. Any online machine on the
tailnet could listen on port 54321, so confirm the selected peer is your Mac;
pairing still requires the token from its QR. The token and video remain
plaintext, so the trusted-LAN caveat below still applies.

## Tailscale scout (GUI)

For a desktop picker instead of the terminal prompt, use the scout:

```bash
./telemachus-scout          # or: ./telemachus-linux --scout
```

It opens a tkinter window listing online Tailscale peers that accept TCP on
the Telemachus port, with **Refresh** to re-scan. Selecting a host and pressing
**Connect** starts `python3 -m telemachus_client --host <ip> --port <port>` as a
subprocess and closes the scout window. The child inherits the saved config
token, so it launches with whatever token is in
`~/.config/telemachus/client.conf`.

If no token is saved yet, **Connect** asks for the `telemachus://` pairing URI
from the Mac, saves the pairing (the URI's token and port, but the **selected
Tailscale peer's address** as host, so the client keeps working when the Mac's
LAN address changes), and launches the child against the saved config. If the
save fails, the child is still launched with `--qr` for that session and the
status line says so. The scout reports missing Tailscale, a missing CLI, no
online peers, or no host with the port open in its status line; the launched
client keeps running after the scout closes.

Install the GUI toolkit if tkinter is absent:

```bash
sudo apt install python3-tk
```

The scout performs the same best-effort, unauthenticated port probe as
`--discover-tailscale`; the security note above applies unchanged.

## User service (systemd)

The scout's two service buttons manage a systemd **user** unit:

- **Install service** writes `~/.config/systemd/user/telemachus-linux.service`
  and runs `systemctl --user daemon-reload`. It never enables the unit.
- **Start service now** runs `systemctl --user start telemachus-linux.service`.

The same file is kept in `systemd/telemachus-linux.service` (a test asserts the
two stay identical). Unit properties:

- `ExecStart=%h/.local/bin/telemachus-linux` — install the client with
  `scripts/install-linux.sh` first, or edit the path.
- `Environment=` carries `PATH`, `XDG_RUNTIME_DIR`, `WAYLAND_DISPLAY`, and
  `DISPLAY`, because a user unit started outside a
  graphical login session cannot inherit them. Adjust `WAYLAND_DISPLAY` if
  your compositor names its socket differently.
- `Restart=on-failure`, `RestartSec=3`, and `PartOf=graphical-session.target`
  (it stops with the session).

Enabling at login is a deliberate, optional step:

```bash
systemctl --user enable telemachus-linux.service   # start at login
systemctl --user status telemachus-linux.service
journalctl --user -u telemachus-linux.service -f
```

There is no `[Install]`-driven enable by default: the unit is written and can
be started, but never enabled on your behalf. A headless start has nothing to
pair with, so pair once first (`--qr`); the unit then reuses the saved token.

## Start the Linux receiver from the Mac

Both computers need Tailscale. Use an OpenSSH server on Linux for the remote
command, and keep the Linux graphical session logged in.

1. Install and pair the Linux client as above, then close its interactive
   receiver window before starting the service.
2. In the Mac's Wireless settings, copy the SSH authorization line from the
   Linux client section.
3. Open `telemachus-scout` on Linux, choose **Authorize Mac**, and paste that
   line. Choose **Install service** as well.
4. On the Mac, select the Linux peer, enter its actual Linux login username,
   and choose **Start Linux client**. The Mac starts its wireless server
   before asking Linux to start `telemachus-linux.service`.

The authorized key has forwarding disabled and a forced command that can
only start this user service. Pasted keys with another command or incompatible
options are rejected. The Mac retains its dedicated SSH identity across starts.

## Choose a Mac virtual display

In Mac settings, choose **New extended display**, set **Virtual displays**
to 1 through 4, then choose **Stream display**. Changing the count or selection
restarts streaming. The desktops are arranged side by side; the protocol
streams one at a time. The Linux `--display` option selects the receiving
monitor on Linux independently of this Mac setting.


## Running

```bash
./telemachus-linux --qr 'telemachus://...'      # first run (pairs + connects)
./telemachus-linux                              # later runs (uses saved config)
./telemachus-linux --display HP                 # force the external HP panel
./telemachus-linux --display 1920x1080@60      # same, by mode
./telemachus-linux --display 1                  # same, by SDL index
./telemachus-linux --windowed                   # window instead of fullscreen
./telemachus-linux --decoder sw               # or: auto, vaapi, cuda
./telemachus-linux --no-input                   # disable mouse forwarding
./telemachus-linux --hud                        # fps / Mbps / RTT overlay
./telemachus-linux --fake-payload               # synthetic frames, no Mac needed
./telemachus-linux --selftest                   # loopback protocol exercise with mock bytes
./telemachus-linux --discover-tailscale         # pick an online Tailscale host
./telemachus-scout                              # GUI Tailscale host picker
```

Display selection: integer index, case-insensitive name substring, or
`WxH@Hz`. With no `--display` the client picks the 1920x1080@60 display,
which disambiguates the external HP (60 Hz) from the internal 144 Hz panel
without hardcoding Wayland connector names.

## HUD

`--hud` (or `F1` at runtime) shows fps / Mbps / RTT in the window title and
logs them about once a second. HUD rendering is deliberately title+log only
so no SDL_ttf dependency is needed.

## Input behavior

- The workstation mouse forwards as a type-2 touch **only while the SDL
  window has focus**.
- Left button: press = DOWN (action 0), drag = MOTION (action 1, throttled
  to ~120 Hz / 8 ms minimum interval), release = UP (action 2).
- Shift+left-drag (or right-drag) synthesises a two-finger gesture
  (`count=2` with `x2,y2` mirrored through the content-rect centre).
- Coordinates are normalised through the same letterbox content-rect math as
  Android's TouchMapper and clamped to 0..1; black-bar areas map to the
  edges, never outside.
- `--no-input` (or `input_enabled=false` in the config) disables forwarding.
- **No keyboard forwarding exists.** The wire protocol defines no keyboard
  message, and this client does not invent one.

## Troubleshooting

- **VAAPI missing -> CUDA -> software.** The client enumerates
  `/dev/dri/renderD*` and tries VAAPI, then NVDEC/CUDA, then software. Check
  which backend won in the log line `decoder backend=...`. Missing groups
  (`video`/`render`) is the usual VAAPI failure; a dead ffmpeg process
  triggers a forced keyframe request plus re-init automatically.
- **Wayland monitor selection.** If fullscreen lands on the wrong panel,
  pass `--display HP`, `--display 1920x1080@60`, or the SDL index explicitly.
  The default rule exists precisely because both panels are 1080p and only
  the refresh rate differs.
- **Token rejected.** Re-pair: put the Mac back in Wireless mode, start
  streaming for a fresh QR, reset the token on the Mac if one is stale, and
  re-run with the new `--qr` URI.
- **No display config.** The client waits ~15 s for the first display-config
  message; firewall rules or connecting while the Mac streams over USB
  (loopback-only) are the common causes.
- **Plaintext.** The token and the video travel unencrypted; use a trusted
  LAN or a direct Ethernet cable, never port-forward the listener.

## Explicit limitations (v1)

- No keyboard forwarding (no wire message for it).
- No audio forwarding.
- Plaintext token + video: trusted LAN / direct Ethernet only.
- `rotation != 0` is best-effort (ffmpeg transpose + a warning in the log).
- Single active client: connecting a second client evicts the first.

## Development checks

```bash
uv run --frozen python -m pytest -q
uv run --frozen ruff check .
uv audit --frozen
```

The tests include real software decoding with ffmpeg and offscreen SDL
presentation when the system libraries are installed. CI runs Python 3.10,
3.12, and 3.14 alongside the existing macOS and Android builds.
