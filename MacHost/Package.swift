// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Telemachus",
    platforms: [
        // Floor is ScreenCaptureKit basics (12.3) + OSAllocatedUnfairLock /
        // SCStreamConfiguration.capturesAudio (13.0). CGVirtualDisplay is a
        // private API present well before 13 — it does NOT require 14.
        .macOS(.v13)
    ],
    products: [
        .executable(
            name: "Telemachus",
            targets: ["Telemachus"])
    ],
    targets: [
        .executableTarget(
            name: "Telemachus",
            dependencies: [],
            path: "Sources",
            cSettings: [
                .unsafeFlags(["-I", "Sources"])
            ],
            swiftSettings: [
                .unsafeFlags(["-Xcc", "-fmodule-map-file=Sources/module.modulemap"])
            ]),
        .testTarget(
            name: "TelemachusTests",
            dependencies: ["Telemachus"],
            path: "Tests/TelemachusTests",
            cSettings: [
                .unsafeFlags(["-I", "Sources"])
            ],
            swiftSettings: [
                .unsafeFlags(["-Xcc", "-fmodule-map-file=Sources/module.modulemap"])
            ]
        )
    ]
)
