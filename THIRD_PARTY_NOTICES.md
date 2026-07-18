# Third-Party Notices

This file is an inventory, not a substitute for the license text shipped by a
dependency. Release artifacts include this file, [NOTICE](NOTICE), and
[LICENSE](LICENSE).

## Upstream project

Telemachus is a fork of
[SideScreen](https://github.com/tranvuongquocdat/SideScreen) by Tran Vuong Quoc
Dat and its contributors, licensed under the MIT License. SideScreen supplied
the foundational virtual display, capture, video transport, Android receiver,
wireless-pairing, and touch implementation. Its original notice is retained in
[LICENSE](LICENSE).

## Android dependencies

The Android build currently declares AndroidX, Material Components, CameraX,
JUnit 4 for tests, and
[ZXing Core 3.5.3](https://github.com/zxing/zxing/tree/zxing-3.5.3).
The shipped AndroidX, Material, CameraX, Kotlin, coroutines, Google utility,
JetBrains annotation, and ZXing runtime artifacts are licensed under the
[Apache License 2.0](licenses/Apache-2.0.txt). JUnit is test-only and is not
packaged in release APKs.

Every Android build generates `ANDROID_RUNTIME_DEPENDENCY_LICENSES.md` from the
resolved `releaseRuntimeClasspath`. The build fails if a runtime dependency
belongs to an unreviewed license group. The report and full Apache-2.0 text are
packaged with the app, and the report is attached to public releases.

## Apple frameworks and unsupported API

The macOS host links Apple system frameworks including AppKit,
ScreenCaptureKit, VideoToolbox, CoreGraphics, Network, and ServiceManagement.
It also calls private `CGVirtualDisplay` APIs to create a virtual display. Those
private APIs are unsupported and may change or stop working in a macOS update.

## Project media

Images under `resources/` and `website/assets/` are project documentation
assets. Contributors must not add screenshots containing private user data,
payment information, or third-party trademarks without permission. Asset
provenance should be recorded in the nearest README before release.

The Telemachus Companion Screens icon was generated from an original project
brief without SideScreen artwork as input. Inherited product screenshots have
been removed from public-facing documentation until accurate Telemachus
replacements can be captured and reviewed for private metadata.
