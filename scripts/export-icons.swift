#!/usr/bin/env swift

import CoreGraphics
import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

private struct Asset {
    let path: String
    let size: Int
    let role: String
    let copyMaster: Bool

    init(path: String, size: Int, role: String, copyMaster: Bool = false) {
        self.path = path
        self.size = size
        self.role = role
        self.copyMaster = copyMaster
    }
}

private enum ExportError: Error, CustomStringConvertible {
    case repositoryRootNotFound
    case cannotReadMaster(String)
    case cannotCreateBitmap(Int)
    case cannotCreateImage
    case cannotCreateDestination(String)
    case cannotFinalizeImage(String)
    case iconutilFailed(Int32)

    var description: String {
        switch self {
        case .repositoryRootNotFound:
            return "Could not find the Telemachus repository root."
        case let .cannotReadMaster(path):
            return "Could not read the icon master at \(path)."
        case let .cannotCreateBitmap(size):
            return "Could not create a \(size) x \(size) bitmap context."
        case .cannotCreateImage:
            return "Could not create an exported CGImage."
        case let .cannotCreateDestination(path):
            return "Could not create a PNG destination at \(path)."
        case let .cannotFinalizeImage(path):
            return "Could not finalize the PNG at \(path)."
        case let .iconutilFailed(status):
            return "iconutil failed with exit status \(status)."
        }
    }
}

private func repositoryRoot() throws -> URL {
    let fileManager = FileManager.default
    let startingPoints = [
        URL(fileURLWithPath: fileManager.currentDirectoryPath),
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
    ]

    for startingPoint in startingPoints {
        var candidate = startingPoint.standardizedFileURL
        while candidate.path != "/" {
            let hasMacHost = fileManager.fileExists(
                atPath: candidate.appendingPathComponent("MacHost").path
            )
            let hasAndroidClient = fileManager.fileExists(
                atPath: candidate.appendingPathComponent("AndroidClient").path
            )
            let hasMaster = fileManager.fileExists(
                atPath: candidate
                    .appendingPathComponent("resources/logo/telemachus-icon-master.png")
                    .path
            )
            if hasMacHost && hasAndroidClient && hasMaster {
                return candidate
            }
            candidate.deleteLastPathComponent()
        }
    }

    throw ExportError.repositoryRootNotFound
}

private func loadImage(from url: URL) throws -> CGImage {
    guard
        let source = CGImageSourceCreateWithURL(url as CFURL, nil),
        let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
    else {
        throw ExportError.cannotReadMaster(url.path)
    }
    return image
}

private func resized(_ source: CGImage, to size: Int) throws -> CGImage {
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    let bitmapInfo = CGBitmapInfo.byteOrder32Big.rawValue
        | CGImageAlphaInfo.premultipliedLast.rawValue

    guard let context = CGContext(
        data: nil,
        width: size,
        height: size,
        bitsPerComponent: 8,
        bytesPerRow: size * 4,
        space: colorSpace,
        bitmapInfo: bitmapInfo
    ) else {
        throw ExportError.cannotCreateBitmap(size)
    }

    context.interpolationQuality = .high
    context.draw(source, in: CGRect(x: 0, y: 0, width: size, height: size))
    guard let image = context.makeImage() else {
        throw ExportError.cannotCreateImage
    }
    return image
}

private func writePNG(_ image: CGImage, to url: URL) throws {
    try FileManager.default.createDirectory(
        at: url.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    guard let destination = CGImageDestinationCreateWithURL(
        url as CFURL,
        UTType.png.identifier as CFString,
        1,
        nil
    ) else {
        throw ExportError.cannotCreateDestination(url.path)
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw ExportError.cannotFinalizeImage(url.path)
    }
}

private func replaceFile(at destination: URL, with source: URL) throws {
    let fileManager = FileManager.default
    try fileManager.createDirectory(
        at: destination.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    if fileManager.fileExists(atPath: destination.path) {
        try fileManager.removeItem(at: destination)
    }
    try fileManager.copyItem(at: source, to: destination)
}

private func sha256(of url: URL) throws -> String {
    SHA256.hash(data: try Data(contentsOf: url))
        .map { String(format: "%02x", $0) }
        .joined()
}

private func createICNS(root: URL) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
    process.arguments = [
        "-c", "icns",
        "-o", root.appendingPathComponent("MacHost/Resources/AppIcon.icns").path,
        root.appendingPathComponent("MacHost/Resources/AppIcon.iconset").path
    ]
    try process.run()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else {
        throw ExportError.iconutilFailed(process.terminationStatus)
    }
}

private func writeManifest(root: URL, master: URL, assets: [Asset]) throws {
    let masterImage = try loadImage(from: master)
    let entries: [[String: Any]] = try assets.map { asset in
        let url = root.appendingPathComponent(asset.path)
        return [
            "height": asset.size,
            "path": asset.path,
            "role": asset.role,
            "sha256": try sha256(of: url),
            "width": asset.size
        ]
    }
    let icnsURL = root.appendingPathComponent("MacHost/Resources/AppIcon.icns")
    let manifest: [String: Any] = [
        "assets": entries,
        "exporter": "scripts/export-icons.swift",
        "identity": "Companion Screens",
        "master": [
            "height": masterImage.height,
            "path": "resources/logo/telemachus-icon-master.png",
            "sha256": try sha256(of: master),
            "width": masterImage.width
        ],
        "provenance": [
            "externalArtworkUsed": false,
            "method": "Original Telemachus outline-icon brief",
            "sideScreenArtworkUsed": false,
            "source": "Approved project-specific raster master"
        ],
        "supplementalAssets": [[
            "path": "MacHost/Resources/AppIcon.icns",
            "role": "macOS packaged icon",
            "sha256": try sha256(of: icnsURL)
        ]]
    ]
    var data = try JSONSerialization.data(
        withJSONObject: manifest,
        options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    )
    data.append(0x0A)
    try data.write(
        to: root.appendingPathComponent("resources/logo/exports-manifest.json"),
        options: .atomic
    )
}

do {
    let root = try repositoryRoot()
    let master = root.appendingPathComponent("resources/logo/telemachus-icon-master.png")
    let masterImage = try loadImage(from: master)
    guard masterImage.width == masterImage.height else {
        throw ExportError.cannotReadMaster("\(master.path) is not square")
    }

    let assets = [
        Asset(path: "resources/logo/main_logo.png", size: masterImage.width, role: "repository and documentation", copyMaster: true),
        Asset(path: "website/assets/main_logo.png", size: 512, role: "website"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_16x16.png", size: 16, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_16x16@2x.png", size: 32, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_32x32.png", size: 32, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_32x32@2x.png", size: 64, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_128x128.png", size: 128, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_128x128@2x.png", size: 256, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_256x256.png", size: 256, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_256x256@2x.png", size: 512, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_512x512.png", size: 512, role: "macOS AppIcon"),
        Asset(path: "MacHost/Resources/AppIcon.iconset/icon_512x512@2x.png", size: 1024, role: "macOS AppIcon")
    ]

    for asset in assets {
        let destination = root.appendingPathComponent(asset.path)
        if asset.copyMaster {
            try replaceFile(at: destination, with: master)
        } else {
            try writePNG(resized(masterImage, to: asset.size), to: destination)
        }
    }
    try createICNS(root: root)
    try writeManifest(root: root, master: master, assets: assets)
    print("Exported \(assets.count) icon assets and AppIcon.icns.")
} catch {
    FileHandle.standardError.write(Data("export-icons: \(error)\n".utf8))
    exit(1)
}
