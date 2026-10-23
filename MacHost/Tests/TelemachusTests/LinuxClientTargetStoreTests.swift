import XCTest
@testable import Telemachus

final class LinuxClientTargetStoreTests: XCTestCase {
    func testTargetAndResultPersistWithoutAssumingUsername() throws {
        let suiteName = "Telemachus.TargetStoreTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = LinuxClientTargetStore(defaults: defaults)
        XCTAssertEqual(store.sshUser, "")
        XCTAssertNil(store.selectedPeerIP)

        store.select(peerName: "Linux receiver", peerIP: "100.64.0.2", sshUser: "receiver")
        store.recordResult("Started")

        let reloaded = LinuxClientTargetStore(defaults: defaults)
        XCTAssertEqual(reloaded.selectedPeerName, "Linux receiver")
        XCTAssertEqual(reloaded.selectedPeerIP, "100.64.0.2")
        XCTAssertEqual(reloaded.sshUser, "receiver")
        XCTAssertEqual(reloaded.lastResult, "Started")
    }
}
