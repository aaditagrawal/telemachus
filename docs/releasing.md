# Release Guide

Releases are intentionally stricter than local development builds. A public
release tag must be a semantic version, have a matching `CHANGELOG.md` section,
and be pushed from a clean commit that has passed CI.

## Declare repository ownership

Repository setup and release ownership:

1. Create the Telemachus-owned repository and configure it as `origin`.
2. Keep the original SideScreen repository as `upstream`.
3. Keep the website's `repository-url` meta value pointed at the canonical
   Telemachus repository.
4. For a public repository, enable GitHub private vulnerability reporting and
   verify the report form before announcing the repository or release.

The release script refuses to push when `origin` is absent, matches `upstream`,
the worktree is dirty, the current branch is not `main`, or local `HEAD`
differs from `origin/main`. The tag workflow independently rejects a tag that
does not point exactly at `origin/main`.

## Android signing

Store the following GitHub Actions secrets:

- `TELEMACHUS_KEYSTORE_BASE64`: base64-encoded release keystore;
- `TELEMACHUS_KEYSTORE_PASSWORD`;
- `TELEMACHUS_KEY_ALIAS`; and
- `TELEMACHUS_KEY_PASSWORD`.

The workflow decodes the keystore into the runner's temporary directory and
passes the corresponding `TELEMACHUS_KEYSTORE_FILE` path to Gradle. Release
assembly fails when any credential is missing, and the workflow rejects the
standard Android debug certificate.

Back up the keystore and passwords offline. Losing them prevents trusted
in-place updates to the existing Android application ID.

Release CI also requires the signing certificate SHA-256 fingerprint to match
the established Telemachus update key:

```text
9202835a99e6d311ae98536d616b7cbbfe8ada31b320d55aa37810a255e1ed71
```

Changing that fingerprint creates an APK that cannot update existing public
installs and therefore requires an explicit application-ID migration.

Pull-request CI uses a one-run ephemeral certificate whose subject explicitly
says `Telemachus CI Ephemeral`. That artifact verifies the release build path
but is not published.

The Android build resolves `releaseRuntimeClasspath`, writes
`ANDROID_RUNTIME_DEPENDENCY_LICENSES.md`, and fails if a runtime dependency
belongs to an unreviewed license group. Review that report whenever dependency
versions change; release CI packages it inside the APK and attaches it to the
GitHub release.

## macOS signing and notarization

For a trusted macOS release, configure all of:

- `TELEMACHUS_MACOS_CERTIFICATE_BASE64`: base64-encoded Developer ID
  Application certificate in PKCS#12 form;
- `TELEMACHUS_MACOS_CERTIFICATE_PASSWORD`;
- `TELEMACHUS_MACOS_SIGNING_IDENTITY`;
- `TELEMACHUS_APPLE_ID`;
- `TELEMACHUS_APPLE_TEAM_ID`; and
- `TELEMACHUS_APP_SPECIFIC_PASSWORD`.

With all six values configured, CI applies the hardened runtime, signs the app
and DMG, submits the DMG to Apple's notary service, staples the ticket, and
validates the result.

When none of the six values are configured, CI may publish an explicitly named
`unsigned-source-build`. That build is ad-hoc signed, is not notarized, and
causes the GitHub release to be marked as a prerelease. Partial signing
configuration fails: CI will never silently downgrade a release that appears
to have been configured for Developer ID distribution.

Contributors can create the same source artifact locally with
`TELEMACHUS_SIGNING_IDENTITY=- ./scripts/package_mac.sh`.

## Publish

Move the completed changes from `Unreleased` into a matching version section.
The release gate permits a standing `Planned` subsection but refuses tags while
completed Added, Changed, Fixed, or Security entries remain unreleased. Commit
and push the clean release commit. After CI passes:

```bash
TELEMACHUS_RELEASE_CONFIRM=0.12.0 ./scripts/release.sh 0.12.0
```

The release workflow publishes the APK, DMG, legal notices and dependency
license text, individual checksums, and a consolidated `SHA256SUMS`. The DMG
filename and release body disclose its trust level. Verify the installed
artifacts on real Intel and Apple Silicon Macs and at least one supported
Android tablet.
