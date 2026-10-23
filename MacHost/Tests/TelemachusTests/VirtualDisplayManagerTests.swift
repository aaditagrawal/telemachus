import XCTest
import CoreGraphics
@testable import Telemachus

final class VirtualDisplayManagerTests: XCTestCase {
    func testNewManagerSelectsNothing() {
        let manager = VirtualDisplayManager()

        XCTAssertEqual(manager.selectedIndex, 0)
        XCTAssertTrue(manager.displayIDs.isEmpty)
        XCTAssertNil(manager.displayID)
        XCTAssertFalse(manager.isActive)
    }

    func testDisplayCountIsClampedToSupportedRange() {
        XCTAssertEqual(VirtualDisplayManager.clampedDisplayCount(0), 1)
        XCTAssertEqual(VirtualDisplayManager.clampedDisplayCount(-3), 1)
        XCTAssertEqual(VirtualDisplayManager.clampedDisplayCount(1), 1)
        XCTAssertEqual(VirtualDisplayManager.clampedDisplayCount(2), 2)
        XCTAssertEqual(VirtualDisplayManager.clampedDisplayCount(4), 4)
        XCTAssertEqual(VirtualDisplayManager.clampedDisplayCount(9), 4)
    }

    func testSelectedIndexIsClampedWhenReading() {
        XCTAssertNil(VirtualDisplayManager.clampedIndex(0, count: 0))
        XCTAssertNil(VirtualDisplayManager.clampedIndex(3, count: 0))
        XCTAssertEqual(VirtualDisplayManager.clampedIndex(-2, count: 3), 0)
        XCTAssertEqual(VirtualDisplayManager.clampedIndex(0, count: 3), 0)
        XCTAssertEqual(VirtualDisplayManager.clampedIndex(2, count: 3), 2)
        XCTAssertEqual(VirtualDisplayManager.clampedIndex(7, count: 3), 2)
        XCTAssertEqual(VirtualDisplayManager.clampedIndex(1, count: 1), 0)
    }

    func testOutOfRangeSelectedIndexDoesNotProduceDisplayID() {
        let manager = VirtualDisplayManager()
        manager.selectedIndex = 5

        XCTAssertNil(manager.displayID)
        XCTAssertFalse(manager.isActive)
    }

    func testDestroyResetsSelection() {
        let manager = VirtualDisplayManager()
        manager.selectedIndex = 2
        manager.destroyDisplay()

        XCTAssertEqual(manager.selectedIndex, 0)
        XCTAssertTrue(manager.displayIDs.isEmpty)
        XCTAssertNil(manager.displayID)
    }

    func testArrangementClearsAllExistingPhysicalDisplayBounds() {
        let origins = VirtualDisplayManager.sideBySideOrigins(
            widths: [1200, 1200, 1200],
            otherDisplayBounds: [
                CGRect(x: 0, y: 0, width: 2560, height: 1440),
                CGRect(x: 2560, y: -200, width: 1920, height: 1080),
                CGRect(x: -1920, y: 0, width: 1920, height: 1080)
            ]
        )

        XCTAssertEqual(origins, [CGPoint(x: 4480, y: 0), CGPoint(x: 5680, y: 0), CGPoint(x: 6880, y: 0)])
    }

    func testArrangementUsesEachDisplaysLogicalWidth() {
        let origins = VirtualDisplayManager.sideBySideOrigins(
            widths: [1000, 1440, 1200], otherDisplayBounds: []
        )

        XCTAssertEqual(origins, [CGPoint(x: 0, y: 0), CGPoint(x: 1000, y: 0), CGPoint(x: 2440, y: 0)])
    }
}
