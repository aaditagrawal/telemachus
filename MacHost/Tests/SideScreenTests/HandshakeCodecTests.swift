import XCTest
@testable import SideScreen

final class HandshakeCodecTests: XCTestCase {
    func testParseValidRequest() throws {
        var bytes: [UInt8] = [0x53, 0x53, 0x57, 0x41]
        bytes.append(contentsOf: (0..<32).map { UInt8($0) })
        bytes.append(8)
        bytes.append(contentsOf: Array("iPad Air".utf8))
        let result = try HandshakeCodec.parseRequest(Data(bytes))
        XCTAssertEqual(result.token.count, 32)
        XCTAssertEqual(result.token.first, 0x00)
        XCTAssertEqual(result.token.last, 0x1F)
        XCTAssertEqual(result.deviceName, "iPad Air")
    }

    func testRejectsBadMagic() {
        var bytes: [UInt8] = [0x58, 0x58, 0x58, 0x58]
        bytes.append(contentsOf: [UInt8](repeating: 0, count: 32))
        bytes.append(1)
        bytes.append(0x41)
        XCTAssertThrowsError(try HandshakeCodec.parseRequest(Data(bytes))) { e in
            XCTAssertEqual(e as? HandshakeError, .invalidMagic)
        }
    }

    func testRejectsZeroNameLength() {
        var bytes: [UInt8] = [0x53, 0x53, 0x57, 0x41]
        bytes.append(contentsOf: [UInt8](repeating: 0, count: 32))
        bytes.append(0)
        XCTAssertThrowsError(try HandshakeCodec.parseRequest(Data(bytes))) { e in
            XCTAssertEqual(e as? HandshakeError, .invalidName)
        }
    }

    func testRejectsNameLengthGreaterThan64() {
        var bytes: [UInt8] = [0x53, 0x53, 0x57, 0x41]
        bytes.append(contentsOf: [UInt8](repeating: 0, count: 32))
        bytes.append(65)
        bytes.append(contentsOf: [UInt8](repeating: 0x41, count: 65))
        XCTAssertThrowsError(try HandshakeCodec.parseRequest(Data(bytes))) { e in
            XCTAssertEqual(e as? HandshakeError, .invalidName)
        }
    }

    func testEncodeOKResponse() {
        let bytes = HandshakeCodec.encodeResponse(status: .ok)
        XCTAssertEqual(Array(bytes), [0x53, 0x53, 0x57, 0x52, 0x00])
    }

    func testEncodeRejectedResponse() {
        XCTAssertEqual(Array(HandshakeCodec.encodeResponse(status: .invalidToken)), [0x53, 0x53, 0x57, 0x52, 0x01])
        XCTAssertEqual(Array(HandshakeCodec.encodeResponse(status: .invalidMagic)), [0x53, 0x53, 0x57, 0x52, 0x02])
        XCTAssertEqual(Array(HandshakeCodec.encodeResponse(status: .invalidName)), [0x53, 0x53, 0x57, 0x52, 0x03])
    }
}
