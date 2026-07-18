# Telemachus

<p align="center">
  <img src="resources/logo/main_logo.png" alt="Telemachus Companion Screens icon" width="144">
</p>

Telemachus turns an Android tablet into a low-latency extended display or
output monitor for macOS.

**Why does this exist?** This exists because I have a Mac Mini running a lot of
tasks for me on Codex. I wanted a solution that would help me have some device
attached to it for immediate extended use. I can monitor what it is doing while
it's performing computer use while I can use my monitor and my laptop for my
daily work. Worked on by GPT 5.6 Sol High.

> [!IMPORTANT]
> **Telemachus is a direct fork of
> [SideScreen](https://github.com/tranvuongquocdat/SideScreen).**
> Tran Vuong Quoc Dat and the SideScreen contributors created the virtual
> display, capture, encoding, transport, decoding, wireless, and touch
> foundation that made this project possible. See [Attribution](#attribution)
> for the full lineage and a detailed account of what this fork changes.

The preferred transport is USB through `adb reverse`. Wireless LAN streaming
is also supported. Video is hardware-encoded with VideoToolbox on the Mac and
hardware-decoded with MediaCodec on Android.

## Features

- A real macOS extended desktop using a virtual display
- Mirroring of the current main display or an existing Screen Sharing display
- Automatic USB setup, Android launch, reconnection, and login startup
- Authenticated wireless pairing through a QR code
- HEVC streaming with automatic H.264 compatibility fallback
- Bounded capture, encode, network, and decode queues that favor current frames
- Touch, drag, scrolling, pinch, long-press, and letterbox-aware input mapping
- Resolution, refresh rate, bitrate, rotation, HiDPI, and quality controls
- Live FPS, bitrate, RTT, decoder latency, frame age, and drop diagnostics
- Guided macOS permission setup and clear Android connection states

## How it works

```text
macOS pixels
  → ScreenCaptureKit (CGDisplayStream fallback)
  → VideoToolbox HEVC/H.264 encoder
  → bounded low-latency TCP stream
  → adb reverse over USB or authenticated LAN
  → Android MediaCodec decoder
  → SurfaceView

Android touch
  → normalized input protocol
  → macOS CGEvent injection
```

USB does not turn an Android tablet into a native DisplayPort or HDMI monitor.
The cable carries compressed video through the Android Debug Bridge. Software
is required on both devices, along with one-time USB-debugging authorization
and macOS permission grants. After setup, reconnecting the cable can restore
the bridge and relaunch the Android receiver automatically.

## Requirements

- macOS 13 or later on Apple Silicon or Intel
- Android 8 / API 26 or later
- A USB data cable
- ADB for USB mode (`brew install android-platform-tools`)
- USB debugging enabled and authorized on the tablet
- Screen Recording permission for the Mac app
- Accessibility permission if tablet touch should control macOS

## Install

When a release is available, download the Mac DMG, Android APK, and
`SHA256SUMS` from
[GitHub Releases](https://github.com/aaditagrawal/telemachus/releases).

Verify the downloads:

```bash
shasum -a 256 -c SHA256SUMS
```

### macOS

1. Open `Telemachus-<version>-mac-universal.dmg`.
2. Drag `Telemachus.app` to `/Applications`.
3. Launch the installed copy.
4. Grant Screen Recording.
5. Grant Accessibility only if tablet touch should control the Mac.
6. Reopen Telemachus after changing either permission.

A filename containing `unsigned-source-build` is an ad-hoc-signed,
non-notarized prerelease. macOS may require **System Settings → Privacy &
Security → Open Anyway**. Only do this when the checksum matches the published
release.

### Android

Open `Telemachus-<version>-android.apk` on the tablet, or install it with ADB:

```bash
adb install -r Telemachus-<version>-android.apk
```

If Android reports `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the installed copy was
signed with a different key. Uninstalling it removes Telemachus settings and
wireless pairing:

```bash
adb uninstall dev.telemachus.display
adb install Telemachus-<version>-android.apk
```

## First USB connection

1. On Android, open **Settings → About tablet → Software information** and tap
   **Build number** seven times.
2. Enable **USB debugging** in **Developer options**.
3. Connect a data-capable cable and accept the Mac's RSA authorization prompt.
4. Confirm that `adb devices` lists the tablet as `device`.
5. Launch Telemachus on both devices.
6. On the Mac, choose **New extended display** or **Current Mac display**.
7. Connect from the Android app in USB mode.

If automatic setup is not active, run:

```bash
./scripts/setup-usb.sh
```

Enable **Launch at Login** for unattended use. **Auto-start streaming** defaults
on, and USB is the default startup mode.

## Build from source

Source builds require a full Xcode/Swift toolchain, JDK 17, Android SDK 34, and
Android platform tools:

```bash
git clone https://github.com/aaditagrawal/telemachus.git
cd telemachus
./scripts/build_mac.sh
./scripts/build_android.sh
./scripts/install_android.sh
```

Install the Mac build in `/Applications` before granting permissions. macOS
associates Screen Recording and Accessibility permission with that app
identity.

## Tested configuration

The primary development tablet is a Samsung SM-P610:

- 2000×1200 landscape panel at 60 Hz
- Exynos hardware HEVC/H.264 decoding
- USB Type-C operating at USB 2.0 High Speed

The default stream is 2000×1200 at 60 Hz and 35 Mb/s HEVC. Gaming Boost uses
45 Mb/s. Both fit comfortably within USB 2.0 bandwidth while avoiding the
latency and thermal cost of oversized buffers.

Other Android 8+ tablets can work with compatible hardware HEVC or H.264
decoders, but they have not all received the same device-level latency,
thermal, and reconnect testing.

## Wireless mode

Select Wireless in the Mac app, start streaming, and scan the displayed QR code
from Android. The QR contains the Mac address, port, and a random 256-bit token.
Non-loopback clients are rejected unless wireless mode is active and the token
matches.

The connection is authenticated but not encrypted. Screen content, telemetry,
touch events, and the token may be visible to a passive network observer. Use
wireless mode only on a trusted LAN and never expose its listener through public
port forwarding. See [docs/threat-model.md](docs/threat-model.md).

## Latency design

Telemachus optimizes for recency rather than queueing every frame:

- ScreenCaptureKit queue depth is two.
- Only one encode operation may wait.
- TCP has Nagle's algorithm disabled.
- Only one video frame may be in flight.
- One dependency-safe successor may wait.
- Broken P-frame backlogs are discarded and replaced with a fresh keyframe.
- Android discards decoded output that is already more than 50 ms stale.

Diagnostics report capture-to-send age, decoder time, RTT, FPS, bitrate, and
drops. Measuring true glass-to-glass latency still requires an external
high-speed camera or photodiode because Mac and tablet clocks are not
synchronized tightly enough for a trustworthy one-way measurement.

## Development

```bash
cd MacHost
swift test
swift run Telemachus --transport-self-test

cd ../AndroidClient
./gradlew testDebugUnitTest lintDebug assembleDebug

cd ..
./scripts/run-capture-cadence-benchmark.sh --duration=15 --cg-only
```

Use a full Xcode installation for the complete Mac test suite. The transport
self-test exercises the production server over loopback.

## Attribution

### SideScreen

Telemachus is a direct MIT-licensed fork—not a clean-room
reimplementation—of
[SideScreen](https://github.com/tranvuongquocdat/SideScreen), created by
[Tran Vuong Quoc Dat](https://github.com/tranvuongquocdat) and developed with
the SideScreen contributors.

SideScreen established the architecture Telemachus continues to use:

- macOS virtual displays through private `CGVirtualDisplay` APIs
- ScreenCaptureKit capture with CGDisplayStream compatibility paths
- VideoToolbox HEVC/H.264 hardware encoding
- TCP transport over LAN and USB through `adb reverse`
- Android MediaCodec decoding into a low-overhead SurfaceView
- wireless discovery and QR/token pairing
- tablet-to-Mac touch and pointer forwarding
- the original host, receiver, protocol, settings, and connection lifecycle

Telemachus would not exist without that work. Credit for those ideas and
implementations belongs to Tran Vuong Quoc Dat and every SideScreen
contributor. The original copyright and MIT License notice remain in
[LICENSE](LICENSE). Additional lineage and dependency notices are in
[NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Attribution is also included in both applications and packaged release
materials.

### Why maintain this fork?

SideScreen is a general Mac-to-Android display project. Telemachus focuses on a
USB-first workstation: a Mac mini running long Codex and Computer Use tasks,
with an otherwise idle Android tablet kept beside it as an immediately
available monitor.

The fork prioritizes appliance-like wired operation, predictable reconnection,
observable latency, stable 60 Hz delivery, and unattended recovery. Wireless
support remains available but is secondary to USB/ADB.

### What Telemachus changes

- Streams the current main display or an existing Screen Sharing display
- Automates ADB forwarding, receiver launch, reconnect, and login startup
- Bounds every frame queue and recovers quickly from stale dependency chains
- Adds live transport, decoder, frame-age, and drop diagnostics
- Preserves aspect ratio and maps touch correctly through letterboxing
- Falls back to H.264 when HEVC hardware decoding is unavailable or broken
- Adds permission onboarding and complete disconnected/loading/error states
- Adds privacy, security, release, licensing, and CI/CD hardening
- Uses the distinct Telemachus Companion Screens identity

The repository preserves SideScreen's privacy-sanitized lineage and original
authorship. Telemachus-specific development is represented by one consolidated
fork commit.

## Documentation

- [Security policy](SECURITY.md)
- [Privacy](PRIVACY.md)
- [Threat model](docs/threat-model.md)
- [Wire protocol](docs/protocol.md)
- [Release guide](docs/releasing.md)
- [Contributing](CONTRIBUTING.md)
- [Support](SUPPORT.md)

## License

Telemachus is available under the [MIT License](LICENSE).
