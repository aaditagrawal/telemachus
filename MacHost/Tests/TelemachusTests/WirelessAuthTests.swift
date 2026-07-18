import XCTest
@testable import Telemachus

final class WirelessAuthTests: XCTestCase {
    private final class MemoryTokenStore: WirelessAuthTokenStore {
        var token: Data?
        var persistError: Error?
        private(set) var persistCount = 0

        func load() throws -> Data? {
            token
        }

        func persist(_ token: Data) throws {
            if let persistError {
                throw persistError
            }
            persistCount += 1
            self.token = token
        }

        func delete() throws {
            token = nil
        }
    }

    private enum TestError: Error {
        case persistFailed
    }

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

    func testPersistAndLoadUseInjectedStore() throws {
        let (defaults, suite) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = MemoryTokenStore()
        let original = WirelessAuth.generateToken()

        try WirelessAuth.persist(
            original,
            store: store,
            legacyDefaults: defaults
        )
        let loaded = try WirelessAuth.load(
            store: store,
            legacyDefaults: defaults
        )

        XCTAssertEqual(loaded, original)
        XCTAssertEqual(store.persistCount, 1)
    }

    func testLoadOrCreateGeneratesOnce() throws {
        let (defaults, suite) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = MemoryTokenStore()

        let first = try WirelessAuth.loadOrCreate(
            store: store,
            legacyDefaults: defaults
        )
        let second = try WirelessAuth.loadOrCreate(
            store: store,
            legacyDefaults: defaults
        )

        XCTAssertEqual(first, second)
        XCTAssertEqual(first.count, 32)
        XCTAssertEqual(store.persistCount, 1)
    }

    func testResetReplacesExistingToken() throws {
        let (defaults, suite) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = MemoryTokenStore()
        let old = Data(repeating: 0x11, count: 32)
        store.token = old

        let fresh = try WirelessAuth.reset(
            store: store,
            legacyDefaults: defaults
        )

        XCTAssertNotEqual(fresh, old)
        XCTAssertEqual(fresh.count, 32)
        XCTAssertEqual(store.token, fresh)
    }

    func testValidLegacyTokenMigratesOnceAndIsErased() throws {
        let (defaults, suite) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = MemoryTokenStore()
        let legacy = Data(repeating: 0x22, count: 32)
        defaults.set(legacy, forKey: WirelessAuth.userDefaultsKey)

        let loaded = try WirelessAuth.load(
            store: store,
            legacyDefaults: defaults
        )
        let loadedAgain = try WirelessAuth.load(
            store: store,
            legacyDefaults: defaults
        )

        XCTAssertEqual(loaded, legacy)
        XCTAssertEqual(loadedAgain, legacy)
        XCTAssertEqual(store.token, legacy)
        XCTAssertEqual(store.persistCount, 1)
        XCTAssertNil(defaults.data(forKey: WirelessAuth.userDefaultsKey))
    }

    func testLegacyTokenIsPreservedIfMigrationFails() {
        let (defaults, suite) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = MemoryTokenStore()
        store.persistError = TestError.persistFailed
        let legacy = Data(repeating: 0x33, count: 32)
        defaults.set(legacy, forKey: WirelessAuth.userDefaultsKey)

        XCTAssertThrowsError(
            try WirelessAuth.load(
                store: store,
                legacyDefaults: defaults
            )
        )
        XCTAssertEqual(
            defaults.data(forKey: WirelessAuth.userDefaultsKey),
            legacy
        )
    }

    func testExistingKeychainTokenRemovesStaleLegacyCopy() throws {
        let (defaults, suite) = isolatedDefaults()
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = MemoryTokenStore()
        let current = Data(repeating: 0x44, count: 32)
        store.token = current
        defaults.set(
            Data(repeating: 0x55, count: 32),
            forKey: WirelessAuth.userDefaultsKey
        )

        let loaded = try WirelessAuth.load(
            store: store,
            legacyDefaults: defaults
        )

        XCTAssertEqual(loaded, current)
        XCTAssertNil(defaults.data(forKey: WirelessAuth.userDefaultsKey))
        XCTAssertEqual(store.persistCount, 0)
    }

    func testValidateConstantTime() {
        let token = WirelessAuth.generateToken()
        XCTAssertTrue(WirelessAuth.validate(token, expected: token))
        var bad = token
        bad[0] ^= 0x01
        XCTAssertFalse(WirelessAuth.validate(bad, expected: token))
        XCTAssertFalse(
            WirelessAuth.validate(
                Data(repeating: 0, count: 31),
                expected: token
            )
        )
    }

    private func isolatedDefaults() -> (UserDefaults, String) {
        let suite = "WirelessAuthTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        return (defaults, suite)
    }
}
