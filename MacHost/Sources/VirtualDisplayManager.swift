import Foundation
import CoreGraphics
import CGVirtualDisplayBridge

/// Manages virtual display creation and lifecycle using CGVirtualDisplay API
class VirtualDisplayManager {
    /// Upper bound on how many virtual displays a single manager will create.
    static let maxDisplayCount = 4

    private var virtualDisplays: [CGVirtualDisplay] = []
    private var adoptedDisplayIDs: [CGDirectDisplayID] = []
    private var displayDescriptors: [CGVirtualDisplayDescriptor] = []
    private var displaySettings: [CGVirtualDisplaySettings] = []

    /// All display IDs owned by this manager, in creation order.
    private(set) var displayIDs: [CGDirectDisplayID] = []

    /// Index into `displayIDs` used by the single-display APIs (mirror, position,
    /// `displayID`). Reads are clamped, so out-of-range values are harmless.
    var selectedIndex: Int = 0

    var displayID: CGDirectDisplayID? {
        guard let index = Self.clampedIndex(selectedIndex, count: displayIDs.count) else {
            return nil
        }
        return displayIDs[index]
    }

    var isActive: Bool {
        return displayID != nil
    }

    /// Clamp a requested display index into `0..<count`.
    /// Returns nil when there are no displays to select.
    static func clampedIndex(_ index: Int, count: Int) -> Int? {
        guard count > 0 else { return nil }
        return min(max(index, 0), count - 1)
    }

    /// Clamp a requested display count into `1...maxDisplayCount`.
    static func clampedDisplayCount(_ count: Int) -> Int {
        return min(max(count, 1), maxDisplayCount)
    }

    /// Create `count` virtual displays side-by-side and select the first.
    /// - Parameters:
    ///   - count: Number of displays to create (clamped to 1...4)
    ///   - width: Display width in pixels
    ///   - height: Display height in pixels
    ///   - refreshRate: Refresh rate in Hz (default: 60)
    ///   - hiDPI: Enable HiDPI mode (default: false)
    ///   - nameBase: Base display name; each display is suffixed with its 1-based index
    func createDisplays(
        count: Int,
        width: Int,
        height: Int,
        refreshRate: Int = 60,
        hiDPI: Bool = false,
        nameBase: String = "Virtual Display"
    ) throws {
        // Clean up existing displays if any
        destroyDisplay()

        guard width > 0, height > 0 else {
            throw VirtualDisplayError.creationFailed(
                "Invalid display dimensions \(width)x\(height)"
            )
        }

        let displayCount = Self.clampedDisplayCount(count)

        // Physical pixels = 2x logical when HiDPI, 1x otherwise
        let physW = hiDPI ? width * 2 : width
        let physH = hiDPI ? height * 2 : height
        // Use width * 10000 + height so (3840,2400) != (2400,3840) — avoids
        // portrait/landscape collision. Each display folds in its index.
        let baseProductID = UInt32((physW * 10000 + physH) & 0xFFFFFFFF)

        for index in 0..<displayCount {
            let productID = baseProductID + UInt32(index)
            let serialNum = 0x0001 + UInt32(index)
            let name = "\(nameBase) \(index + 1)"

            // CGVirtualDisplay can remain registered with WindowServer across a
            // host restart. Reuse the exact Telemachus display instead of trying
            // to create a duplicate, which the private API rejects.
            if let existingID = Self.findExistingDisplay(
                productID: productID,
                serialNum: serialNum,
                width: physW,
                height: physH,
                refreshRate: refreshRate
            ) {
                adoptedDisplayIDs.append(existingID)
                displayIDs.append(existingID)
                debugLog(
                    "Reusing existing Telemachus virtual display \(existingID) " +
                    "(\(name)): \(physW)x\(physH) @ \(refreshRate)Hz"
                )
                continue
            }

            // Create display descriptor
            let descriptor = CGVirtualDisplayDescriptor()
            descriptor.name = name
            descriptor.maxPixelsWide = UInt32(physW)
            descriptor.maxPixelsHigh = UInt32(physH)

            // HiDPI needs high PPI so macOS recognises as Retina (≥200 PPI threshold)
            // non-HiDPI stays at 110 PPI (typical tablet assumption)
            let ppi: Double = hiDPI ? 220.0 : 110.0
            descriptor.sizeInMillimeters = CGSize(
                width: Double(physW) * 25.4 / ppi,
                height: Double(physH) * 25.4 / ppi
            )

            descriptor.productID = productID
            descriptor.vendorID = 0xEEEE
            descriptor.serialNum = serialNum

            displayDescriptors.append(descriptor)

            // Create display settings
            let settings = CGVirtualDisplaySettings()
            settings.hiDPI = hiDPI ? 1 : 0

            // HiDPI: anchor mode (physical) + logical mode
            // The anchor tells macOS "this display is high-density" → unlocks HiDPI for logical mode
            // non-HiDPI: single mode at requested resolution
            var modes: [CGVirtualDisplayMode] = []
            if hiDPI {
                modes.append(CGVirtualDisplayMode(
                    width: UInt32(physW),
                    height: UInt32(physH),
                    refreshRate: Double(refreshRate)
                ))
            }
            modes.append(CGVirtualDisplayMode(
                width: UInt32(width),
                height: UInt32(height),
                refreshRate: Double(refreshRate)
            ))
            settings.modes = modes

            displaySettings.append(settings)

            // Create virtual display
            guard let display = CGVirtualDisplay(descriptor: descriptor) else {
                destroyDisplay()
                throw VirtualDisplayError.creationFailed(
                    "Failed to create CGVirtualDisplay \(index + 1) of \(displayCount)"
                )
            }

            virtualDisplays.append(display)

            // Apply settings
            guard display.apply(settings) else {
                destroyDisplay()
                throw VirtualDisplayError.settingsApplyFailed(
                    "Failed to apply settings to display \(index + 1) of \(displayCount)"
                )
            }

            displayIDs.append(display.displayID)

            let modeDesc = hiDPI
                ? "\(width)x\(height) HiDPI (physical \(physW)x\(physH))"
                : "\(width)x\(height)"
            print(
                "✅ Virtual display \(index + 1)/\(displayCount) created: " +
                "\(modeDesc) @ \(refreshRate)Hz (ID: \(display.displayID))"
            )
        }

        arrangeSideBySide(width: width)
        selectedIndex = 0
    }

    /// Create a single virtual display with specified configuration
    /// - Parameters:
    ///   - width: Display width in pixels
    ///   - height: Display height in pixels
    ///   - refreshRate: Refresh rate in Hz (default: 60)
    ///   - hiDPI: Enable HiDPI mode (default: false)
    ///   - name: Display name (default: "Virtual Display")
    func createDisplay(
        width: Int,
        height: Int,
        refreshRate: Int = 60,
        hiDPI: Bool = false,
        name: String = "Virtual Display"
    ) throws {
        try createDisplays(
            count: 1,
            width: width,
            height: height,
            refreshRate: refreshRate,
            hiDPI: hiDPI,
            nameBase: name
        )
    }

    /// Place the group to the right of existing desktops, in logical coordinates.
    /// Arrangement is best-effort: a failure leaves the displays usable and
    /// recoverable with `setDisplayPosition`.
    private func arrangeSideBySide(width: Int) {
        guard displayIDs.count > 1 else { return }

        var displayCount: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &displayCount) == .success else {
            debugLog("⚠️  Could not read active displays for arrangement")
            return
        }
        var activeDisplays = [CGDirectDisplayID](repeating: 0, count: Int(displayCount))
        guard CGGetActiveDisplayList(displayCount, &activeDisplays, &displayCount) == .success else {
            debugLog("⚠️  Could not read active display bounds for arrangement")
            return
        }
        let managedIDs = Set(displayIDs)
        let otherBounds = activeDisplays.prefix(Int(displayCount))
            .filter { !managedIDs.contains($0) }
            .map { CGDisplayBounds($0) }
        let widths = displayIDs.map { id in
            let logicalWidth = CGDisplayBounds(id).width
            return logicalWidth > 0 ? logicalWidth : CGFloat(width)
        }
        do {
            try setDisplayPositions(Self.sideBySideOrigins(widths: widths, otherDisplayBounds: otherBounds))
            print("📍 \(displayIDs.count) virtual displays arranged side-by-side")
        } catch {
            debugLog("⚠️  Could not complete display arrangement: \(error)")
        }
    }

    static func sideBySideOrigins(widths: [CGFloat], otherDisplayBounds: [CGRect]) -> [CGPoint] {
        var x = max(0, otherDisplayBounds.map(\.maxX).max() ?? 0)
        return widths.map { width in
            let origin = CGPoint(x: x, y: 0)
            x += width
            return origin
        }
    }

    private func setDisplayPositions(_ origins: [CGPoint]) throws {
        var config: CGDisplayConfigRef?
        guard CGBeginDisplayConfiguration(&config) == .success, let config else {
            throw VirtualDisplayError.configurationFailed("Failed to begin display arrangement")
        }
        for (id, origin) in zip(displayIDs, origins) {
            let result = CGConfigureDisplayOrigin(config, id, Int32(origin.x), Int32(origin.y))
            guard result == .success else {
                CGCancelDisplayConfiguration(config)
                throw VirtualDisplayError.configurationFailed("Failed to position display \(id): \(result)")
            }
        }
        guard CGCompleteDisplayConfiguration(config, .forSession) == .success else {
            throw VirtualDisplayError.configurationFailed("Failed to complete display arrangement")
        }
    }

    private static func findExistingDisplay(
        productID: UInt32,
        serialNum: UInt32,
        width: Int,
        height: Int,
        refreshRate: Int
    ) -> CGDirectDisplayID? {
        var onlineDisplays = [CGDirectDisplayID](repeating: 0, count: 16)
        var displayCount: UInt32 = 0
        guard CGGetOnlineDisplayList(16, &onlineDisplays, &displayCount) == .success else {
            return nil
        }

        return onlineDisplays.prefix(Int(displayCount)).first { displayID in
            guard CGDisplayVendorNumber(displayID) == 0xEEEE,
                  CGDisplayModelNumber(displayID) == productID,
                  CGDisplaySerialNumber(displayID) == serialNum,
                  Int(CGDisplayPixelsWide(displayID)) == width,
                  Int(CGDisplayPixelsHigh(displayID)) == height,
                  let mode = CGDisplayCopyDisplayMode(displayID) else {
                return false
            }
            return abs(mode.refreshRate - Double(refreshRate)) < 0.5
        }
    }

    /// Clone the main display configuration
    func cloneMainDisplay() throws {
        guard let mainDisplay = CGMainDisplayID() as CGDirectDisplayID? else {
            throw VirtualDisplayError.mainDisplayNotFound
        }

        let width = Int(CGDisplayPixelsWide(mainDisplay))
        let height = Int(CGDisplayPixelsHigh(mainDisplay))

        // Get refresh rate
        var refreshRate = 60
        if let mode = CGDisplayCopyDisplayMode(mainDisplay) {
            refreshRate = Int(mode.refreshRate)
        }

        try createDisplay(
            width: width,
            height: height,
            refreshRate: refreshRate,
            name: "Virtual Display (Clone)"
        )
    }

    /// Enable mirror mode with main display for the selected display
    func enableMirrorMode() throws {
        guard let displayID else {
            throw VirtualDisplayError.displayNotCreated
        }

        let mainDisplay = CGMainDisplayID()

        var config: CGDisplayConfigRef?
        let beginResult = CGBeginDisplayConfiguration(&config)

        guard beginResult == CGError.success, let config = config else {
            throw VirtualDisplayError.configurationFailed("Failed to begin display configuration")
        }

        let mirrorResult = CGConfigureDisplayMirrorOfDisplay(
            config,
            displayID,
            mainDisplay
        )

        if mirrorResult != CGError.success {
            CGCancelDisplayConfiguration(config)
            throw VirtualDisplayError.mirrorModeFailed("Failed to configure mirror mode: \(mirrorResult)")
        }

        let completeResult = CGCompleteDisplayConfiguration(config, .forSession)

        if completeResult != CGError.success {
            throw VirtualDisplayError.mirrorModeFailed("Failed to complete mirror configuration: \(completeResult)")
        }

        print("✅ Mirror mode enabled")
    }

    /// Disable mirror mode (extend mode) for the selected display
    func disableMirrorMode() throws {
        guard let displayID else {
            throw VirtualDisplayError.displayNotCreated
        }

        var config: CGDisplayConfigRef?
        let beginResult = CGBeginDisplayConfiguration(&config)

        guard beginResult == CGError.success, let config = config else {
            throw VirtualDisplayError.configurationFailed("Failed to begin display configuration")
        }

        // Setting mirror to kCGNullDirectDisplay disables mirroring
        let mirrorResult = CGConfigureDisplayMirrorOfDisplay(
            config,
            displayID,
            CGDirectDisplayID(kCGNullDirectDisplay)
        )

        if mirrorResult != CGError.success {
            CGCancelDisplayConfiguration(config)
            throw VirtualDisplayError.mirrorModeFailed("Failed to disable mirror mode: \(mirrorResult)")
        }

        let completeResult = CGCompleteDisplayConfiguration(config, .forSession)

        if completeResult != CGError.success {
            throw VirtualDisplayError.mirrorModeFailed("Failed to complete configuration: \(completeResult)")
        }

        print("✅ Extend mode enabled (mirror disabled)")
    }

    /// Get the selected display's current position (origin)
    func getDisplayPosition() -> CGPoint? {
        guard let displayID = displayID else { return nil }
        let bounds = CGDisplayBounds(displayID)
        return bounds.origin
    }

    /// Set the selected display's position in arrangement
    func setDisplayPosition(x: Int32, y: Int32) throws {
        guard let displayID else {
            throw VirtualDisplayError.displayNotCreated
        }

        var config: CGDisplayConfigRef?
        let beginResult = CGBeginDisplayConfiguration(&config)

        guard beginResult == CGError.success, let config = config else {
            throw VirtualDisplayError.configurationFailed("Failed to begin display configuration")
        }

        let originResult = CGConfigureDisplayOrigin(config, displayID, x, y)

        if originResult != CGError.success {
            CGCancelDisplayConfiguration(config)
            throw VirtualDisplayError.configurationFailed("Failed to set display origin: \(originResult)")
        }

        let completeResult = CGCompleteDisplayConfiguration(config, .forSession)

        if completeResult != CGError.success {
            throw VirtualDisplayError.configurationFailed("Failed to complete configuration: \(completeResult)")
        }

        print("📍 Display position set to (\(x), \(y))")
    }

    /// Save the group's first display as its anchor, independent of stream selection.
    func saveDisplayPosition() {
        guard let firstDisplayID = displayIDs.first else { return }
        let position = CGDisplayBounds(firstDisplayID).origin
        let defaults = UserDefaults.standard
        defaults.set(Int(position.x), forKey: "Telemachus_positionX")
        defaults.set(Int(position.y), forKey: "Telemachus_positionY")
        defaults.set(true, forKey: "Telemachus_hasPosition")
        print("💾 Saved display position: (\(Int(position.x)), \(Int(position.y)))")
    }

    /// Restore the group together so selecting another stream cannot overlap its neighbors.
    func restoreDisplayPosition() {
        let defaults = UserDefaults.standard
        guard defaults.bool(forKey: "Telemachus_hasPosition") else {
            print("📍 No saved display position found")
            return
        }

        let x = defaults.integer(forKey: "Telemachus_positionX")
        let y = defaults.integer(forKey: "Telemachus_positionY")

        do {
            if displayIDs.count > 1 {
                let widths = displayIDs.map { CGDisplayBounds($0).width }
                let origins = Self.sideBySideOrigins(widths: widths, otherDisplayBounds: []).map {
                    CGPoint(x: $0.x + CGFloat(x), y: CGFloat(y))
                }
                try setDisplayPositions(origins)
            } else {
                try setDisplayPosition(x: Int32(x), y: Int32(y))
            }
            print("📍 Restored display position: (\(x), \(y))")
        } catch {
            print("⚠️  Failed to restore display position: \(error)")
        }
    }

    /// Verify the selected virtual display is registered in the system display list
    func verifyDisplayRegistered() -> Bool {
        guard let displayID = displayID else {
            debugLog("verifyDisplayRegistered: no displayID set")
            return false
        }

        var onlineDisplays = [CGDirectDisplayID](repeating: 0, count: 16)
        var displayCount: UInt32 = 0
        let err = CGGetOnlineDisplayList(16, &onlineDisplays, &displayCount)

        guard err == .success else {
            debugLog("verifyDisplayRegistered: CGGetOnlineDisplayList failed with \(err)")
            return false
        }

        let onlineIDs = Array(onlineDisplays.prefix(Int(displayCount)))
        let found = onlineIDs.contains(displayID)
        debugLog("verifyDisplayRegistered: displayID \(displayID) \(found ? "FOUND" : "NOT FOUND") in online displays \(onlineIDs)")
        return found
    }

    /// Destroy all virtual displays managed by this instance
    func destroyDisplay() {
        let destroyedCount = virtualDisplays.count + adoptedDisplayIDs.count
        virtualDisplays.removeAll()
        adoptedDisplayIDs.removeAll()
        displayDescriptors.removeAll()
        displaySettings.removeAll()
        displayIDs.removeAll()
        selectedIndex = 0

        if destroyedCount > 0 {
            print("🗑️  \(destroyedCount) virtual display(s) destroyed")
        }
    }

    deinit {
        destroyDisplay()
    }
}

// MARK: - Error Types
enum VirtualDisplayError: Error, LocalizedError {
    case creationFailed(String)
    case settingsApplyFailed(String)
    case displayNotCreated
    case mainDisplayNotFound
    case configurationFailed(String)
    case mirrorModeFailed(String)

    var errorDescription: String? {
        switch self {
        case .creationFailed(let msg):
            return "Virtual display creation failed: \(msg)"
        case .settingsApplyFailed(let msg):
            return "Settings apply failed: \(msg)"
        case .displayNotCreated:
            return "Virtual display has not been created"
        case .mainDisplayNotFound:
            return "Main display not found"
        case .configurationFailed(let msg):
            return "Display configuration failed: \(msg)"
        case .mirrorModeFailed(let msg):
            return "Mirror mode operation failed: \(msg)"
        }
    }
}
