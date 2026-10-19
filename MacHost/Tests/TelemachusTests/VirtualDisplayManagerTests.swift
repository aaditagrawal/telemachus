import XCTest
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
}
