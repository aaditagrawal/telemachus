import XCTest
@testable import Telemachus

final class StreamGeometryTests: XCTestCase {
    func testSixteenByNineSourceFitsTabletWithoutStretching() {
        let result = AppDelegate.aspectFitStreamSize(
            sourceWidth: 3840,
            sourceHeight: 2160,
            maximumWidth: 2000,
            maximumHeight: 1200
        )
        XCTAssertEqual(result.width, 2000)
        XCTAssertEqual(result.height, 1124)
    }

    func testPortraitSourceIsHeightBound() {
        let result = AppDelegate.aspectFitStreamSize(
            sourceWidth: 1200,
            sourceHeight: 2000,
            maximumWidth: 2000,
            maximumHeight: 1200
        )
        XCTAssertEqual(result.width, 720)
        XCTAssertEqual(result.height, 1200)
    }

    func testEncoderDimensionsAreEven() {
        let result = AppDelegate.aspectFitStreamSize(
            sourceWidth: 1365,
            sourceHeight: 767,
            maximumWidth: 1999,
            maximumHeight: 1199
        )
        XCTAssertEqual(result.width % 2, 0)
        XCTAssertEqual(result.height % 2, 0)
        XCTAssertLessThanOrEqual(result.width, 1999)
        XCTAssertLessThanOrEqual(result.height, 1199)
    }

    func testExistingDisplayIsNotUpscaledPastItsSourceResolution() {
        let result = AppDelegate.aspectFitStreamSize(
            sourceWidth: 1920,
            sourceHeight: 1080,
            maximumWidth: 2000,
            maximumHeight: 1200
        )
        XCTAssertEqual(result.width, 1920)
        XCTAssertEqual(result.height, 1080)
    }
}
