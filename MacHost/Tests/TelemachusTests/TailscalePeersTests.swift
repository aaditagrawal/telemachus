import XCTest
@testable import Telemachus

final class TailscalePeersTests: XCTestCase {
    private func fixture() -> [String: Any] {
        [
            "Self": [
                "HostName": "adityas-macbook-air",
                "TailscaleIPs": ["100.96.63.21", "fd7a:115c:a1e0::1"]
            ],
            "Peer": [
                "node-1": [
                    "HostName": "asus-zorin",
                    "TailscaleIPs": ["100.96.42.5", "fd7a:115c:a1e0::5"],
                    "OS": "linux",
                    "Online": true
                ],
                "node-2": [
                    "HostName": "workstation",
                    "TailscaleIPs": ["100.65.52.94"],
                    "OS": "linux",
                    "Online": false
                ],
                "node-3": [
                    "DNSName": "tablet.tailnet.ts.net.",
                    "TailscaleIPs": ["fd7a:115c:a1e0::9"],
                    "OS": "android",
                    "Online": true
                ]
            ]
        ]
    }

    func testParsesPeersWithOnlineFirst() {
        let peers = TailscaleStatus.parsePeers(json: fixture())
        XCTAssertEqual(peers.count, 3)
        // Online peers sort before offline ones.
        XCTAssertEqual(peers.first?.name, "asus-zorin")
        XCTAssertEqual(peers.last?.name, "workstation")
        XCTAssertFalse(peers.last?.online ?? true)
    }

    func testPeerFields() {
        let peers = TailscaleStatus.parsePeers(json: fixture())
        let asus = peers.first { $0.name == "asus-zorin" }
        XCTAssertEqual(asus?.ip, "100.96.42.5", "Must pick the IPv4 entry")
        XCTAssertEqual(asus?.os, "linux")
        XCTAssertTrue(asus?.online ?? false)
    }

    func testFallsBackToDNSLabelWhenHostNameMissing() {
        let peers = TailscaleStatus.parsePeers(json: fixture())
        let tablet = peers.first { $0.os == "android" }
        XCTAssertEqual(tablet?.name, "tablet", "DNSName trailing dot and domain stripped")
    }

    func testPeerWithOnlyIPv6HasEmptyIP() {
        let peers = TailscaleStatus.parsePeers(json: fixture())
        let tablet = peers.first { $0.os == "android" }
        XCTAssertEqual(tablet?.ip, "", "IPv6-only node has no IPv4 for a pairing URL")
    }

    func testMissingPeerMapIsEmpty() {
        XCTAssertTrue(TailscaleStatus.parsePeers(json: ["Self": [:]]).isEmpty)
        XCTAssertTrue(TailscaleStatus.parsePeers(json: [:]).isEmpty)
    }

    func testSelfTailscaleIPPicksFirstIPv4() {
        XCTAssertEqual(TailscaleStatus.parseTailscaleIP(json: fixture()), "100.96.63.21")
    }

    func testSelfTailscaleIPNilWhenNoSelf() {
        XCTAssertNil(TailscaleStatus.parseTailscaleIP(json: ["Peer": [:]]))
    }

    func testSelfTailscaleIPNilWhenIPv6Only() {
        let json: [String: Any] = ["Self": ["TailscaleIPs": ["fd7a:115c:a1e0::1"]]]
        XCTAssertNil(TailscaleStatus.parseTailscaleIP(json: json))
    }

    func testPeerIsIdentifiableByIP() {
        let peer = TailscalePeer(name: "box", ip: "100.64.0.1", os: "linux", online: true)
        XCTAssertEqual(peer.id, "100.64.0.1")
        let addressless = TailscalePeer(name: "box", ip: "", os: "", online: false)
        XCTAssertEqual(addressless.id, "box")
    }
}
