import XCTest
import Foundation
import Network
@testable import Telemachus

final class StreamingServerLifecycleTests: XCTestCase {
    private let queue = DispatchQueue(
        label: "StreamingServerLifecycleTests",
        qos: .userInitiated
    )

    func testSecondListenerReportsPortConflict() throws {
        let port = testPort(offset: 1)
        let first = StreamingServer(port: port)
        let second = StreamingServer(port: port)
        defer {
            first.stop()
            second.stop()
        }

        try first.start()
        XCTAssertThrowsError(try second.start(timeout: 1))
    }

    func testFragmentedWirelessHandshakeIsAccepted() throws {
        let port = testPort(offset: 2)
        let token = Data(repeating: 0xA5, count: 32)
        let server = StreamingServer(
            port: port,
            mode: .wireless(authToken: token)
        )
        defer { server.stop() }

        let paired = expectation(description: "fragmented handshake accepted")
        server.onWirelessClientPaired = { name in
            XCTAssertEqual(name, "Test tablet")
            paired.fulfill()
        }
        try server.start()

        let client = try readyClient(port: port)
        defer { client.cancel() }
        let request = handshakeRequest(token: token, name: "Test tablet")
        for (index, byte) in request.enumerated() {
            queue.asyncAfter(deadline: .now() + .milliseconds(index)) {
                client.send(
                    content: Data([byte]),
                    completion: .contentProcessed { _ in }
                )
            }
        }

        wait(for: [paired], timeout: 2)
    }

    func testUnauthenticatedCandidateDoesNotEvictActiveClient() throws {
        let port = testPort(offset: 3)
        let token = Data(repeating: 0x5A, count: 32)
        let server = StreamingServer(
            port: port,
            mode: .wireless(authToken: token)
        )
        defer { server.stop() }

        let connected = expectation(description: "legitimate client connected")
        let disconnected = expectation(description: "active client disconnected")
        disconnected.isInverted = true
        server.onClientConnected = { connected.fulfill() }
        server.onClientDisconnected = { disconnected.fulfill() }
        try server.start()

        let legitimate = try readyClient(port: port)
        defer { legitimate.cancel() }
        legitimate.send(
            content: handshakeRequest(token: token, name: "Legitimate"),
            completion: .contentProcessed { _ in }
        )
        wait(for: [connected], timeout: 2)

        let rogue = try readyClient(port: port)
        defer { rogue.cancel() }
        rogue.send(content: Data([0x00]), completion: .contentProcessed { _ in })

        wait(for: [disconnected], timeout: 0.5)
    }

    func testIncompleteHandshakeTimesOut() throws {
        let port = testPort(offset: 6)
        let server = StreamingServer(
            port: port,
            mode: .wireless(authToken: Data(repeating: 0x45, count: 32))
        )
        defer { server.stop() }
        try server.start()

        let client = try readyClient(port: port)
        defer { client.cancel() }
        let closed = expectation(description: "incomplete handshake closed")
        client.receive(minimumIncompleteLength: 1, maximumLength: 1) {
            _, _, isComplete, error in
            if isComplete || error != nil {
                closed.fulfill()
            }
        }
        wait(for: [closed], timeout: 4)
    }

    func testTokenRotationDisconnectsAuthenticatedClient() throws {
        let port = testPort(offset: 4)
        let token = Data(repeating: 0x12, count: 32)
        let server = StreamingServer(
            port: port,
            mode: .wireless(authToken: token)
        )
        defer { server.stop() }

        let connected = expectation(description: "client connected")
        let disconnected = expectation(description: "client revoked")
        server.onClientConnected = { connected.fulfill() }
        server.onClientDisconnected = { disconnected.fulfill() }
        try server.start()

        let client = try readyClient(port: port)
        defer { client.cancel() }
        client.send(
            content: handshakeRequest(token: token, name: "Revoked"),
            completion: .contentProcessed { _ in }
        )
        wait(for: [connected], timeout: 2)

        server.rotateAuthToken(Data(repeating: 0x34, count: 32))
        wait(for: [disconnected], timeout: 2)
    }

    func testReplacingConnectionIgnoresStaleCancellationCallback() throws {
        let port = testPort(offset: 5)
        let token = Data(repeating: 0x77, count: 32)
        let server = StreamingServer(
            port: port,
            mode: .wireless(authToken: token)
        )
        defer { server.stop() }

        let firstConnected = expectation(description: "first connected")
        let secondConnected = expectation(description: "second connected")
        var connectionCount = 0
        server.onClientConnected = {
            connectionCount += 1
            if connectionCount == 1 {
                firstConnected.fulfill()
            } else if connectionCount == 2 {
                secondConnected.fulfill()
            }
        }
        let disconnected = expectation(description: "new session disconnected")
        disconnected.isInverted = true
        server.onClientDisconnected = { disconnected.fulfill() }
        try server.start()

        let first = try readyClient(port: port)
        defer { first.cancel() }
        first.send(
            content: handshakeRequest(token: token, name: "First"),
            completion: .contentProcessed { _ in }
        )
        wait(for: [firstConnected], timeout: 2)

        let second = try readyClient(port: port)
        defer { second.cancel() }
        second.send(
            content: handshakeRequest(token: token, name: "Second"),
            completion: .contentProcessed { _ in }
        )
        wait(for: [secondConnected], timeout: 2)
        wait(for: [disconnected], timeout: 0.5)
    }

    private func readyClient(port: UInt16) throws -> NWConnection {
        let ready = expectation(description: "client ready")
        var failure: Error?
        let client = NWConnection(
            host: NWEndpoint.Host("127.0.0.1"),
            port: NWEndpoint.Port(rawValue: port)!,
            using: .tcp
        )
        client.stateUpdateHandler = { state in
            switch state {
            case .ready:
                ready.fulfill()
            case .failed(let error):
                failure = error
                ready.fulfill()
            default:
                break
            }
        }
        client.start(queue: queue)
        wait(for: [ready], timeout: 2)
        if let failure { throw failure }
        return client
    }

    private func handshakeRequest(token: Data, name: String) -> Data {
        let nameData = Data(name.utf8)
        var request = Data(HandshakeCodec.requestMagic)
        request.append(token)
        request.append(UInt8(nameData.count))
        request.append(nameData)
        return request
    }

    private func testPort(offset: UInt16) -> UInt16 {
        56_000 + UInt16(ProcessInfo.processInfo.processIdentifier % 500) + offset
    }
}
