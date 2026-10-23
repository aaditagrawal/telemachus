import XCTest
@testable import Telemachus

final class DisplaySettingsTests: XCTestCase {
    private let countKey = "Telemachus_virtualDisplayCount"
    private let indexKey = "Telemachus_streamedDisplayIndex"
    private var suiteName = ""
    private var defaults = UserDefaults.standard

    override func setUp() {
        super.setUp()
        suiteName = "Telemachus.DisplaySettingsTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testDefaultsToSingleDisplayStreamingTheFirst() {
        let settings = DisplaySettings(defaults: defaults)

        XCTAssertEqual(settings.virtualDisplayCount, 1)
        XCTAssertEqual(settings.streamedDisplayIndex, 0)
        XCTAssertEqual(settings.linuxSSHUser, "")
    }

    func testVirtualDisplayCountIsClampedWhenLoaded() {
        defaults.set(9, forKey: countKey)
        XCTAssertEqual(DisplaySettings(defaults: defaults).virtualDisplayCount, VirtualDisplayManager.maxDisplayCount)

        defaults.set(0, forKey: countKey)
        XCTAssertEqual(DisplaySettings(defaults: defaults).virtualDisplayCount, 1)
    }

    func testStreamedDisplayIndexIsClampedToCountWhenLoaded() {
        defaults.set(2, forKey: countKey)
        defaults.set(-5, forKey: indexKey)
        XCTAssertEqual(DisplaySettings(defaults: defaults).streamedDisplayIndex, 0)

        defaults.set(9, forKey: indexKey)
        XCTAssertEqual(DisplaySettings(defaults: defaults).streamedDisplayIndex, 1)
    }

    func testMultiDisplaySettingsPersist() {
        let settings = DisplaySettings(defaults: defaults)
        settings.virtualDisplayCount = 3
        settings.streamedDisplayIndex = 2

        let reloaded = DisplaySettings(defaults: defaults)
        XCTAssertEqual(reloaded.virtualDisplayCount, 3)
        XCTAssertEqual(reloaded.streamedDisplayIndex, 2)
    }

    func testReducingCountClampsAndPersistsSelection() {
        let settings = DisplaySettings(defaults: defaults)
        settings.virtualDisplayCount = 4
        settings.streamedDisplayIndex = 3
        settings.virtualDisplayCount = 2

        XCTAssertEqual(settings.streamedDisplayIndex, 1)
        XCTAssertEqual(DisplaySettings(defaults: defaults).streamedDisplayIndex, 1)

        settings.virtualDisplayCount = 4
        XCTAssertEqual(settings.streamedDisplayIndex, 1)
    }

    func testAssignedCountAndIndexStayWithinBounds() {
        let settings = DisplaySettings(defaults: defaults)
        settings.virtualDisplayCount = 99
        settings.streamedDisplayIndex = 99
        XCTAssertEqual(settings.virtualDisplayCount, 4)
        XCTAssertEqual(settings.streamedDisplayIndex, 3)

        settings.virtualDisplayCount = -9
        XCTAssertEqual(settings.virtualDisplayCount, 1)
        XCTAssertEqual(settings.streamedDisplayIndex, 0)
        settings.streamedDisplayIndex = -7
        XCTAssertEqual(settings.streamedDisplayIndex, 0)
        XCTAssertEqual(defaults.integer(forKey: countKey), 1)
        XCTAssertEqual(defaults.integer(forKey: indexKey), 0)
    }

    func testResetRestoresSingleDisplayAndPersistsIt() {
        let settings = DisplaySettings(defaults: defaults)
        settings.displaySource = .extended
        settings.virtualDisplayCount = 4
        settings.streamedDisplayIndex = 3
        settings.resetToDefaults()

        XCTAssertEqual(settings.virtualDisplayCount, 1)
        XCTAssertEqual(settings.streamedDisplayIndex, 0)
        XCTAssertEqual(settings.displaySource, .currentMain)
        let reloaded = DisplaySettings(defaults: defaults)
        XCTAssertEqual(reloaded.virtualDisplayCount, 1)
        XCTAssertEqual(reloaded.streamedDisplayIndex, 0)
    }

    func testExplicitLinuxSSHUsernamePersists() {
        let settings = DisplaySettings(defaults: defaults)
        settings.linuxSSHUser = "receiver"

        XCTAssertEqual(DisplaySettings(defaults: defaults).linuxSSHUser, "receiver")
    }
}
