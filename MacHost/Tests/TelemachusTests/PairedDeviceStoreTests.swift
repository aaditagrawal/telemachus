import XCTest
@testable import Telemachus

final class PairedDeviceStoreTests: XCTestCase {
    private func freshStore() -> PairedDeviceStore {
        let suite = "PairedDeviceStoreTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        return PairedDeviceStore(defaults: defaults)
    }

    func testStartsEmpty() {
        XCTAssertEqual(freshStore().all().count, 0)
    }

    func testUpsertAdds() {
        let store = freshStore()
        store.upsert(name: "iPad Air", lastConnected: Date(timeIntervalSince1970: 1000))
        XCTAssertEqual(store.all().count, 1)
        XCTAssertEqual(store.all().first?.name, "iPad Air")
    }

    func testUpsertUpdatesExisting() {
        let store = freshStore()
        store.upsert(name: "iPad Air", lastConnected: Date(timeIntervalSince1970: 1000))
        store.upsert(name: "iPad Air", lastConnected: Date(timeIntervalSince1970: 2000))
        XCTAssertEqual(store.all().count, 1)
        XCTAssertEqual(store.all().first?.lastConnected.timeIntervalSince1970, 2000)
    }

    func testForgetRemoves() {
        let store = freshStore()
        store.upsert(name: "iPad Air", lastConnected: Date())
        store.upsert(name: "Pixel Tablet", lastConnected: Date())
        store.forget(name: "iPad Air")
        XCTAssertEqual(store.all().map { $0.name }, ["Pixel Tablet"])
    }

    func testClearRemovesAll() {
        let store = freshStore()
        store.upsert(name: "iPad Air", lastConnected: Date())
        store.upsert(name: "Pixel Tablet", lastConnected: Date())
        store.clear()
        XCTAssertEqual(store.all().count, 0)
    }

    func testSortedByLastConnectedDescending() {
        let store = freshStore()
        store.upsert(name: "Old", lastConnected: Date(timeIntervalSince1970: 1000))
        store.upsert(name: "New", lastConnected: Date(timeIntervalSince1970: 9000))
        store.upsert(name: "Mid", lastConnected: Date(timeIntervalSince1970: 5000))
        XCTAssertEqual(store.all().map { $0.name }, ["New", "Mid", "Old"])
    }

    func testRoundTripJSON() {
        let suite = "PairedDeviceStoreTests-RT-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        let storeA = PairedDeviceStore(defaults: defaults)
        storeA.upsert(name: "iPad Air", lastConnected: Date(timeIntervalSince1970: 1000))
        let storeB = PairedDeviceStore(defaults: defaults)
        XCTAssertEqual(storeB.all().first?.name, "iPad Air")
    }
}
