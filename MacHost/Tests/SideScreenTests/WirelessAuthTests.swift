import XCTest
@testable import SideScreen

final class WirelessAuthTests: XCTestCase {
    func testTokenIs32Bytes() {
        let token = WirelessAuth.generateToken()
        XCTAssertEqual(token.count, 32)
    }

    func testTwoTokensDiffer() {
        let a = WirelessAuth.generateToken()
        let b = WirelessAuth.generateToken()
        XCTAssertNotEqual(a, b, "Random token collision is astronomically unlikely")
    }

    func testTokenBytesHaveEntropy() {
        let token = WirelessAuth.generateToken()
        let unique = Set(token)
        XCTAssertGreaterThan(unique.count, 15)
    }

    func testPersistAndLoad() {
        let suite = "WirelessAuthTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)

        let original = WirelessAuth.generateToken()
        WirelessAuth.persist(original, defaults: defaults)
        let loaded = WirelessAuth.load(defaults: defaults)
        XCTAssertEqual(loaded, original)
    }

    func testLoadOrCreateGeneratesIfMissing() {
        let suite = "WirelessAuthTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)

        let first = WirelessAuth.loadOrCreate(defaults: defaults)
        let second = WirelessAuth.loadOrCreate(defaults: defaults)
        XCTAssertEqual(first, second, "loadOrCreate must return the persisted value on second call")
        XCTAssertEqual(first.count, 32)
    }

    func testValidateConstantTime() {
        let token = WirelessAuth.generateToken()
        XCTAssertTrue(WirelessAuth.validate(token, expected: token))
        var bad = token
        bad[0] ^= 0x01
        XCTAssertFalse(WirelessAuth.validate(bad, expected: token))
        XCTAssertFalse(WirelessAuth.validate(Data(repeating: 0, count: 31), expected: token))
    }
}
