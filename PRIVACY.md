# Privacy

Telemachus streams a selected Mac display to an Android device. That stream may
contain any information visible on the selected display. If touch input is
enabled, the tablet sends pointer and gesture input back to the Mac.

## Data handled by Telemachus

- Encoded display frames, display dimensions, codec negotiation, and
  performance telemetry move between the two devices.
- Wireless pairing places the Mac address, port, display name, and a random
  bearer token in a QR code. The Android client stores the paired host and
  token locally so it can reconnect.
- USB mode uses Android Debug Bridge port forwarding and device discovery.
- The Mac stores application preferences locally and keeps the wireless pairing
  token in the login Keychain as a non-synchronizing, device-only item.

The Telemachus project does not operate an analytics, advertising, account, or
cloud relay service. The stream is sent directly between the Mac and tablet.
Wireless traffic is not encrypted; other participants on an untrusted network
may be able to observe it.

## Camera and QR scanning

The Android camera is used only while scanning a pairing QR code. QR decoding
uses the open-source ZXing Core library locally on the tablet. No camera frames
are intentionally persisted or sent to a project-operated service.

## Permissions

- **Screen Recording (macOS):** required to capture the chosen display.
- **Accessibility (macOS):** optional; required only for tablet-driven input.
- **Local Network (macOS):** required only for wireless mode.
- **Camera (Android):** required only for QR pairing.
- **USB debugging (Android):** required for USB transport and automatic launch.

Revoking a permission disables the related feature. Resetting wireless pairing
invalidates the old token on the Mac; forget the paired host on Android as
well.

## Backups and logs

The Mac may write diagnostic information to local system logs and to a private,
size-limited file under `~/Library/Logs/Telemachus`. The Android app writes an
app-private `files/diag.log`, rotates it at approximately 1 MB to
`diag.log.old`, and may include host addresses, ports, display dimensions,
codec state, and decoder diagnostics. Android removes those files when the app
is uninstalled or its storage is cleared.

Do not attach logs publicly without reviewing them for device names, addresses,
and usage details. Pairing credentials are excluded from Android backups;
report a build that restores them as a security bug. While streaming, the
Android window is marked secure so Mac screen content is excluded from Android
screenshots and Recents previews.
