import XCTest
@testable import Telemachus

/// Covers the multi-display settings added for N virtual displays
/// (`virtualDisplayCount`, `streamedDisplayIndex`): defaults, persistence,
/// and the clamping applied when loading stored values.
final class DisplaySettingsTests: XCTestCase {
    private let countKey = "Telemachus_virtualDisplayCount"
    private let indexKey = "Telemachus_streamedDisplayIndex"

    override func setUp() {
        super.setUp()
        UserDefaults.standard.removeObject(forKey: countKey)
        UserDefaults.standard.removeObject(forKey: indexKey)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: countKey)
        UserDefaults.standard.removeObject(forKey: indexKey)
        super.tearDown()
    }

    func testDefaultsToSingleDisplayStreamingTheFirst() {
        let settings = DisplaySettings()

        XCTAssertEqual(settings.virtualDisplayCount, 1)
        XCTAssertEqual(settings.streamedDisplayIndex, 0)
    }

    func testVirtualDisplayCountIsClampedWhenLoaded() {
        UserDefaults.standard.set(9, forKey: countKey)
        XCTAssertEqual(DisplaySettings().virtualDisplayCount, VirtualDisplayManager.maxDisplayCount)

        UserDefaults.standard.set(0, forKey: countKey)
        XCTAssertEqual(DisplaySettings().virtualDisplayCount, 1)
    }

    func testNegativeStreamedDisplayIndexIsClampedWhenLoaded() {
        UserDefaults.standard.set(-5, forKey: indexKey)
        XCTAssertEqual(DisplaySettings().streamedDisplayIndex, 0)
    }

    func testMultiDisplaySettingsPersist() {
        let settings = DisplaySettings()
        settings.virtualDisplayCount = 3
        settings.streamedDisplayIndex = 2

        let reloaded = DisplaySettings()
        XCTAssertEqual(reloaded.virtualDisplayCount, 3)
        XCTAssertEqual(reloaded.streamedDisplayIndex, 2)
    }
}
