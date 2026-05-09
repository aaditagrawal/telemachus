import XCTest
@testable import SideScreen

final class PairingURLTests: XCTestCase {
    func testBuildContainsAllFields() {
        let token = Data((0..<32).map { UInt8($0) })
        let url = PairingURL.build(host: "192.168.1.42", port: 8888, token: token, name: "Dat's MacBook")
        XCTAssertTrue(url.hasPrefix("sidescreen://192.168.1.42:8888?"))
        XCTAssertTrue(url.contains("t="))
        XCTAssertTrue(url.contains("name="))
    }

    func testTokenIsBase64URLNoPadding() {
        let token = Data((0..<32).map { _ in UInt8(0xAB) })
        let url = PairingURL.build(host: "1.2.3.4", port: 9, token: token, name: "x")
        let tValue = url.split(separator: "?")[1]
            .split(separator: "&")
            .first { $0.hasPrefix("t=") }!
            .dropFirst(2)
        XCTAssertEqual(tValue.count, 43)
        XCTAssertFalse(tValue.contains("="))
        XCTAssertFalse(tValue.contains("+"))
        XCTAssertFalse(tValue.contains("/"))
    }

    func testNameIsURLEncoded() {
        let token = Data(repeating: 0, count: 32)
        let url = PairingURL.build(host: "1.2.3.4", port: 9, token: token, name: "Dat's MacBook")
        XCTAssertTrue(url.contains("name=Dat%27s%20MacBook") || url.contains("name=Dat's%20MacBook"))
    }
}
