# Telemachus visual identity

## Companion Screens

The Telemachus icon is a deliberately simple outline of a laptop and companion
tablet joined by one mint link. It describes the product directly without
reusing SideScreen artwork or relying on platform-specific logos.

The full-bleed graphite master lets Android and macOS apply their own launcher
masks. The symbol uses broad, rounded strokes so it remains recognizable at
small launcher and Finder sizes.

## Source and exports

- `telemachus-icon-master.png`: approved 1254 px raster master
- `main_logo.png`: repository and documentation mark
- `scripts/export-icons.swift`: deterministic platform export tool
- `MacHost/Resources/AppIcon.iconset/`: macOS size exports
- `MacHost/Resources/AppIcon.icns`: packaged macOS icon
- `AndroidClient/app/src/main/res/mipmap-anydpi*/`: adaptive, round, and themed Android icons
- `website/assets/main_logo.png`: 512 px web export
- `exports-manifest.json`: dimensions, checksums, and provenance

The icon was generated from an original Telemachus brief and selected for this
project on 2026-07-18. No SideScreen artwork or third-party logo was supplied as
an input. The platform-safe master extends the graphite background to the
canvas edges while preserving the selected laptop, tablet, and mint connector.

Regenerate raster platform exports on macOS:

```sh
./scripts/export-icons.swift
```
