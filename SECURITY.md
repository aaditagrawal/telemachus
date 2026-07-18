# Security Policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose a
screen, pairing token, input-control channel, signing credential, or private
user data.

Use GitHub's private
[Report a vulnerability](https://github.com/aaditagrawal/telemachus/security/advisories/new)
flow. Maintainers must keep private vulnerability reporting enabled before the
public repository is announced. GitHub exposes this setting for public
repositories, so maintainers must enable and verify it immediately after the
visibility change. If the private form is unavailable, open a minimal issue
asking a maintainer to restore it without including exploit details.

Include the affected version or commit, transport mode, macOS and Android
versions, reproduction steps, and the practical impact. Maintainers will
acknowledge a report when it is seen and coordinate disclosure after a fix is
available. No fixed response-time SLA is promised by this volunteer project.

## Security boundaries

USB mode uses ADB loopback forwarding and depends on the security of the Mac,
the Android device, the cable, and the user's ADB authorization.

Wireless mode authenticates a client with a bearer token but does **not**
encrypt the token, video, telemetry, or touch events. Use wireless mode only on
a network where every participant is trusted. See
[the threat model](docs/threat-model.md) before enabling it.

Telemachus can capture displays and, when Accessibility access is granted,
inject pointer events. Treat both applications and release-signing credentials
as security-sensitive.

## Supported versions

Security fixes are provided on a best-effort basis for the latest release and
the default branch. Older releases may need to upgrade.
