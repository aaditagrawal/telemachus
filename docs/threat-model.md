# Threat Model

## Protected assets

- pixels visible on the selected Mac display;
- the wireless bearer token and paired-host record;
- the ability to inject pointer input through macOS Accessibility;
- release-signing credentials and update provenance; and
- device names, network addresses, diagnostics, and application preferences.

## Trust assumptions

USB mode trusts the Mac, tablet, physical cable, ADB installation, and the
user's one-time Android debugging authorization. ADB access is powerful beyond
Telemachus and should be revoked for an untrusted computer.

Wireless mode currently trusts every participant and network device on the
LAN. It authenticates with a bearer token but does not provide confidentiality,
server authentication, forward secrecy, or replay protection. A passive
observer may see display content, touch events, telemetry, and the credential.

The Mac and Android operating systems, hardware codec implementations, Apple
system frameworks, and declared third-party dependencies are trusted.

## Threats and current controls

| Threat | Current control | Residual risk |
| --- | --- | --- |
| Unauthorized USB viewer | ADB device authorization and loopback transport | A compromised local process or authorized computer remains trusted |
| Unauthorized LAN viewer/controller | Random 256-bit bearer token | Token and content are plaintext and replayable |
| Connection eviction or resource exhaustion | Length bounds and a single active stream | Admission must complete before replacing the trusted client; rate limiting is limited |
| Malformed network input | Fixed message types and bounded fields | Native codec and parser bugs remain possible |
| Credential recovery from backup | Pairing reset | Android backup must exclude paired-host credentials |
| Malicious release substitution | Platform signing when configured, published checksums | Unsigned source builds require users to verify provenance manually |
| Private API breakage | Documented macOS floor and local fallback behavior | `CGVirtualDisplay` is unsupported and can change without notice |

## Deployment guidance

- Prefer USB.
- Use wireless only on a private trusted LAN; never expose the listener through
  port forwarding, a public IP, or an untrusted hotspot.
- Disable touch input when output-only viewing is sufficient.
- Re-pair after a token may have appeared in a screenshot, log, QR photo, or
  backup.
- Install releases only from the canonical repository once it is declared, and
  verify published checksums.

## Security roadmap

Before describing wireless mode as safe on an untrusted network, add a
standard, reviewed authenticated-encryption protocol with mutual endpoint
authentication, replay protection, protocol-version negotiation, and secure
key storage. Do not design a custom cipher.

Fuzz message framing and authentication, test connection-admission races,
exclude credentials from backup, and exercise hostile codec/configuration
inputs in CI.
