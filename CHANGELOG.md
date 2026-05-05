# Changelog

All notable changes to Side Screen will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- WiFi Direct support (wireless mode)
- Audio streaming
- Multi-touch gestures
- Stylus/pen support

---

<a id="0.7.0"></a>
## [0.7.0] - 2026-05-05

User-experience improvements (permission recovery, display arrangement) and pipeline performance work to reduce input lag on high-resolution tablets.

### Fixed
- **Stuck Screen Recording permission after reinstall** (#8): when macOS holds onto a stale TCC entry from a previous SideScreen install, `CGRequestScreenCaptureAccess()` no-ops silently and the user is locked out. The Status section now detects this state (preflight returns false despite a previous successful grant), surfaces a "Permission stuck" banner, and offers a one-click "Reset Permission" button that runs `tccutil reset ScreenCapture com.sidescreen.app`. If the spawn fails, a fallback banner shows the exact command with a Copy button.
- **Input lag on dynamic content / high-res tablets** (#13): the encoder previously used all-intra (every frame a keyframe), producing 3-5x more data per frame than necessary. This saturated tablet decode/compose pipelines at high panel resolutions and starved Mac WindowServer rendering when capturing fast-changing content. Switched to short-GOP IPP encoding (1 keyframe per second, P-frames in between), which keeps frame-loss recovery within 1 second over reliable USB-C TCP while dramatically lowering per-frame work end-to-end.
- **Touch parsing wasted CPU when touch was disabled**: incoming touch frames from the client were parsed and dispatched to the main queue even when host-side touch control was off; only the `guard` in the handler discarded them. Touch frames now drop early without parsing or dispatch when `touchEnabled` is off; ping/pong continues unaffected.
- **Slow first frame after client connects on idle screen**: with short-GOP encoding, a client connecting during a static screen would wait up to a full second for the next scheduled keyframe before its decoder could start. The host now forces an IDR keyframe the moment a client appears, replays the last cached pixel buffer if capture is currently idle, and drops orphan P-frames at the streaming server until that first keyframe is sent — so a fresh decoder always starts on a sync frame. (Cherry-picked from #15 — thanks to @luisdavim for the contribution and @busybox11 for testing.)

### Changed
- **Default refresh rate is now 60 Hz** for new installs (was 120 Hz). 120 fps stream on a 120 Hz tablet leaves zero VSync headroom — any pipeline jitter immediately queues frames. 60 fps gives 2:1 headroom on 120 Hz panels and a 16.7 ms budget on 60 Hz panels. Existing users keep their saved value; users can still opt back to 120 from settings.
- **Display Configuration UI**: refresh-rate selector moved into its own section, full-width custom buttons replace the native segmented picker, and the resolution list height now adapts to whether "Show all" is on.

### Added
- **"Arrange Displays…" shortcut** (#12) in the Display Configuration section. Opens System Settings → Displays directly on the arrangement pane.
- **Decoder pipeline latency log** on the Android client. Every 60 output frames, DiagLog records average/max decoder input-to-output latency plus available input buffer count, so users reporting lag can attach a log that pinpoints whether the bottleneck is decoder queuing, compose/present, or upstream Mac.

---

<a id="0.6.8"></a>
## [0.6.8] - 2026-03-18

Bug fixes — connection reliability and stream stability.

### Fixed
- **ADB race condition on first connect**: `setupADBReverse()` now completes before the streaming server starts. Previously, the server could begin listening before the ADB tunnel was established, causing the tablet to show "Mac Server Running" in red on first install. Includes automatic retry (up to 3×) to handle first-time USB authorization delays.
- **SCStream false-positive restart on idle screen**: When the display was idle (no content changes), macOS stops delivering frames as an optimization. The frame flow monitor incorrectly treated this as a stream crash and triggered unnecessary restarts, eventually falling back to CGDisplayStream. The monitor now sends a keepalive frame from the last captured buffer instead of restarting. Real SCStream errors are still handled via the error delegate.

---

<a id="0.6.5"></a>
## [0.6.5] - 2026-03-17

HiDPI support and Universal Binary.

### Added
- **HiDPI / Retina mode**: Virtual display now supports HiDPI scaling. macOS renders at 2× physical pixels for sharp, Retina-quality output — even at lower logical resolutions (e.g. choose 1280×800 logical on a 2K tablet for comfortable UI size with full sharpness).
- **Universal Binary**: Mac app now ships as a Universal Binary (arm64 + x86_64), supporting both Apple Silicon and Intel Macs natively.

---

<a id="0.6.2"></a>
## [0.6.2] - 2026-03-17

Universal Binary build system.

### Changed
- Build pipeline updated to produce Universal Binary (arm64 + x86_64).

---

<a id="0.5.2"></a>
## [0.5.2] - 2026-02-21

Documentation update — ADB prerequisite instructions.

### Added
- ADB installation guide in README and website for users who don't have `adb` installed (Homebrew + `android-platform-tools`)
- Clarified that the Mac app requires `adb` to show "Running" status

### Website
- Added ADB prerequisite note to download section

---

<a id="0.2.3"></a>
## [0.2.3] - 2026-02-19

Packaging and documentation fixes.

### Fixed
- Ad-hoc code signing for macOS DMG to reduce Gatekeeper issues
- Gatekeeper workaround (`xattr -cr`) added to website, README, and release notes
- Removed outdated `TAASD` folder references from README and CONTRIBUTING
- Simplified installation guide — users download from GitHub Releases instead of building from source
- Removed redundant terminal code block from website "How It Works" section

---

<a id="0.2.2"></a>
## [0.2.2] - 2026-02-19

Bug fixes and UX improvements for website and DMG installer.

### Fixed
- DMG installer now includes Applications folder shortcut for drag-and-drop installation
- Theme toggle button vertically centered in website header
- Hero action buttons no longer overlap with stats section above
- Removed outdated `adb reverse` manual instructions — Mac app handles port forwarding automatically

### Improved
- Faster scroll-in animations (0.6s → 0.3s) for snappier website experience
- Updated website FAQ and README to reflect automatic ADB setup

---

<a id="1.1.0"></a>
## [1.1.0] - 2026-02-19

Performance overhaul targeting sub-30ms end-to-end latency.

### Performance Improvements
- SCStream `queueDepth` optimization (-33ms worst-case capture latency)
- Async MediaCodec API with Choreographer vsync alignment on Android
- Pipeline decoupling: capture, encode, and send stages now run independently
- Timestamp accuracy fixes on both macOS and Android
- TCP_NODELAY and BufferedInputStream optimizations for network layer
- Touch path latency reduction (removed verbose logging from hot path)

### Developer Experience
- SwiftLint integration for macOS codebase
- ktlint integration for Android codebase
- GitHub Actions CI/CD for automated builds and lint checks
- Professional README with badges, hero section, and structured docs

### Website
- Updated performance claims to reflect <30ms latency target
- Added hero stats section with latency, FPS, and codec info
- Placeholder image instructions for all screenshot locations

---

<a id="1.0.0"></a>
## [1.0.0] - 2025-12-27

Initial public release of Side Screen.

### Features

#### macOS Host
- Virtual display creation using CGVirtualDisplay API
- Screen capture with ScreenCaptureKit
- H.265/HEVC hardware encoding via VideoToolbox
- TCP streaming server (port 8888)
- Settings window with Apple design language
  - Resolution selection (1920x1200, 1920x1080, custom)
  - Frame rate options (30, 60, 90, 120 FPS)
  - Bitrate control (10-50 Mbps)
  - Quality presets (Low, Medium, High)
- Gaming Boost mode for optimized low-latency streaming
- Menu bar integration with real-time performance stats

#### Android Client
- H.265/HEVC hardware decoding via MediaCodec
- TCP client with automatic reconnection
- Full-screen video rendering
- Touch input with prediction for latency compensation
- Floating draggable settings button
- Real-time stats overlay (FPS, bitrate, resolution)
- Performance mode for sustained CPU/GPU performance
- Device rotation support
- Material Design 3 UI

### Technical Highlights
- Hardware-accelerated video pipeline on both platforms
- TCP_NODELAY for minimum network latency
- Frame dropping for frames older than 50ms
- Input prediction using linear extrapolation
- High-priority threads for display operations
- Choreographer-based vsync alignment on Android

---

## Version History Format

Each release follows this format:

```
## [Version] - YYYY-MM-DD

### New Features
- Feature descriptions

### Improvements
- Performance and UX improvements

### Bug Fixes
- Bug fix descriptions

### Breaking Changes
- Any breaking changes (if applicable)
```

---

[Unreleased]: https://github.com/tranvuongquocdat/SideScreen/compare/0.6.8...HEAD
[0.6.8]: https://github.com/tranvuongquocdat/SideScreen/compare/0.6.5...0.6.8
[0.6.5]: https://github.com/tranvuongquocdat/SideScreen/compare/0.6.2...0.6.5
[0.6.2]: https://github.com/tranvuongquocdat/SideScreen/compare/0.5.2...0.6.2
[0.5.2]: https://github.com/tranvuongquocdat/SideScreen/compare/0.2.3...0.5.2
[0.2.3]: https://github.com/tranvuongquocdat/SideScreen/compare/0.2.2...0.2.3
[0.2.2]: https://github.com/tranvuongquocdat/SideScreen/compare/0.2.1...0.2.2
[0.2.1]: https://github.com/tranvuongquocdat/SideScreen/compare/0.2.0...0.2.1
[0.2.0]: https://github.com/tranvuongquocdat/SideScreen/compare/0.1.0...0.2.0
[0.1.0]: https://github.com/tranvuongquocdat/SideScreen/releases/tag/0.1.0
