# Public Repository Checklist

Do not change repository visibility until every blocking item is complete.

## Blocking

- [x] Publish from a sanitized Git history. Removed inherited media blobs
      contained precise capture metadata, including location and device details.
      Replacing the working-tree files does not remove those blobs from older
      commits. Create the public repository from a reviewed squash commit, or
      coordinate a `git filter-repo` rewrite and force-push while the repository
      is still private.
- [x] Re-scan the complete publishable history with Gitleaks and an EXIF audit of
      every tracked image.
- [x] Replace the canonical GitHub repository with a fresh private repository
      from the flattened history. Verify that it has no pull-request refs and
      that Issues are disabled before changing visibility.
- [ ] As soon as repository visibility is public, enable private vulnerability
      reporting and verify the `security/advisories/new` form while signed out
      of the maintainer account. Do this before announcing the public repository.

The history rewrite was completed on 2026-07-18 with `git-filter-repo` 2.47.0.
The rewritten tree was byte-for-byte identical to the reviewed pre-rewrite
`main` tree. Full-history Gitleaks and EXIF scans found no remaining secrets,
GPS coordinates, camera make/model fields, or serial identifiers in
publishable refs.

The canonical GitHub repository was recreated from the flattened history on
2026-07-19. The replacement started with no pull requests or issues, and its
issue tracker is disabled.

## Release trust

- [ ] Configure all Developer ID and notarization secrets before publishing a
      stable Mac release. Until then, keep `unsigned-source-build` releases
      clearly labeled as prereleases.
- [ ] Preserve the checked-in Android signing-certificate SHA-256 check so new
      releases remain upgrade-compatible with existing installs.
- [ ] Review the generated Android runtime dependency-license report attached to
      every release.
- [x] Pin SwiftLint and verify the downloaded archive checksum in CI.

## Final verification

- [ ] Run macOS tests, transport self-test, universal build, packaging, signing,
      and Gatekeeper assessment.
- [ ] Run Android unit tests, lint, formatting, debug/release assembly, signature
      verification, and installation on a clean device.
- [ ] Verify every release checksum, packaged legal notice, repository link,
      support link, and installation command from a fresh checkout.
