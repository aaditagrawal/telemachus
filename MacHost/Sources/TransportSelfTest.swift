import Foundation
import Network

/// Loopback smoke test for the exact production transport implementation.
/// It validates startup capability negotiation, display configuration, a
/// metadata-bearing keyframe, ping/pong, and client-to-host touch parsing.
enum TransportSelfTest {
    private final class ResultState {
        let lock = NSLock()
        var receivedConfig = false
        var receivedKeyframe = false
        var receivedPong = false
        var receivedTouch = false
        var failure: String?

        var isComplete: Bool {
            lock.withLock {
                receivedConfig && receivedKeyframe && receivedPong && receivedTouch
            }
        }
    }

    static func run() -> Bool {
        let port: UInt16 = 55432
        let state = ResultState()
        let server = StreamingServer(port: port)
        server.setDisplaySize(width: 2000, height: 1124)
        server.onTouchEvent = { x, y, action, pointers, _, _ in
            state.lock.withLock {
                state.receivedTouch =
                    abs(x - 0.25) < 0.001 &&
                    abs(y - 0.75) < 0.001 &&
                    action == 1 &&
                    pointers == 1
            }
        }
        server.onClientConnected = {
            let keyframe = Data([0, 0, 0, 1, 0x26, 0x01, 0xAA, 0x55])
            server.sendFrame(
                keyframe,
                timestamp: DispatchTime.now().uptimeNanoseconds,
                isKeyframe: true
            )
        }
        do {
            try server.start()
        } catch {
            print("Transport self-test: FAIL (listener startup: \(error))")
            return false
        }

        let conflictingServer = StreamingServer(port: port)
        let portConflictRejected: Bool
        do {
            try conflictingServer.start(timeout: 1)
            portConflictRejected = false
        } catch {
            portConflictRejected = true
        }
        conflictingServer.stop()
        guard portConflictRejected else {
            server.stop()
            print("Transport self-test: FAIL (second listener reused active port)")
            return false
        }

        let queue = DispatchQueue(label: "transport-self-test", qos: .userInteractive)
        let client = NWConnection(
            host: NWEndpoint.Host("127.0.0.1"),
            port: NWEndpoint.Port(rawValue: port)!,
            using: .tcp
        )
        var buffer = Data()

        func receiveNext() {
            client.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { data, _, complete, error in
                if let data {
                    buffer.append(data)
                    parseServerMessages(buffer: &buffer, state: state)
                }
                if let error {
                    state.lock.withLock { state.failure = error.localizedDescription }
                    return
                }
                if complete {
                    if !state.isComplete {
                        state.lock.withLock { state.failure = "Server closed before all messages arrived" }
                    }
                    return
                }
                receiveNext()
            }
        }

        client.stateUpdateHandler = { connectionState in
            switch connectionState {
            case .ready:
                // Opt in to frame metadata before the host finishes startup.
                client.send(content: Data([8]), completion: .contentProcessed { error in
                    if let error {
                        state.lock.withLock { state.failure = error.localizedDescription }
                        return
                    }

                    var messages = Data([4])
                    var pingValue: UInt64 = 0x0102_0304_0506_0708
                    withUnsafeBytes(of: &pingValue) { messages.append(contentsOf: $0) }
                    messages.append(2)
                    messages.append(1)
                    var x: Float = 0.25
                    var y: Float = 0.75
                    var action: Int32 = 1
                    withUnsafeBytes(of: &x) { messages.append(contentsOf: $0) }
                    withUnsafeBytes(of: &y) { messages.append(contentsOf: $0) }
                    withUnsafeBytes(of: &action) { messages.append(contentsOf: $0) }
                    client.send(content: messages, completion: .contentProcessed { _ in })
                })
                receiveNext()

            case .failed(let error):
                state.lock.withLock { state.failure = error.localizedDescription }

            default:
                break
            }
        }
        client.start(queue: queue)

        // Touch callbacks intentionally arrive on the main queue, so keep its
        // run loop moving instead of blocking it on a semaphore.
        let deadline = Date(timeIntervalSinceNow: 5)
        while Date() < deadline {
            let done = state.lock.withLock {
                state.failure != nil ||
                    (state.receivedConfig &&
                     state.receivedKeyframe &&
                     state.receivedPong &&
                     state.receivedTouch)
            }
            if done { break }
            RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 0.01))
        }
        client.cancel()
        server.stop()

        let snapshot = state.lock.withLock {
            (
                state.receivedConfig,
                state.receivedKeyframe,
                state.receivedPong,
                state.receivedTouch,
                state.failure
            )
        }
        let passed = snapshot.0 && snapshot.1 && snapshot.2 && snapshot.3 &&
            snapshot.4 == nil
        print(
            "Transport self-test: \(passed ? "PASS" : "FAIL") " +
            "(config=\(snapshot.0), keyframe=\(snapshot.1), " +
            "pong=\(snapshot.2), touch=\(snapshot.3), portConflict=true, " +
            "error=\(snapshot.4 ?? "none"))"
        )
        return passed
    }

    private static func parseServerMessages(buffer: inout Data, state: ResultState) {
        while let type = buffer.first {
            switch type {
            case 1:
                guard buffer.count >= 13 else { return }
                let width = readInt32(buffer, offset: 1)
                let height = readInt32(buffer, offset: 5)
                let rotation = readInt32(buffer, offset: 9)
                state.lock.withLock {
                    state.receivedConfig = width == 2000 && height == 1124 && rotation == 0
                }
                buffer.removeFirst(13)

            case 5:
                guard buffer.count >= 9 else { return }
                state.lock.withLock { state.receivedPong = true }
                buffer.removeFirst(9)

            case 6:
                guard buffer.count >= 14 else { return }
                let payloadSize = Int(readInt32(buffer, offset: 1))
                guard payloadSize >= 0, buffer.count >= 14 + payloadSize else { return }
                let flags = buffer[buffer.index(buffer.startIndex, offsetBy: 5)]
                let payloadStart = buffer.index(buffer.startIndex, offsetBy: 14)
                let payloadEnd = buffer.index(payloadStart, offsetBy: payloadSize)
                let payload = buffer[payloadStart..<payloadEnd]
                state.lock.withLock {
                    state.receivedKeyframe =
                        flags & 1 == 1 &&
                        payload.elementsEqual([0, 0, 0, 1, 0x26, 0x01, 0xAA, 0x55])
                }
                buffer.removeFirst(14 + payloadSize)

            default:
                state.lock.withLock { state.failure = "Unexpected server message type \(type)" }
                return
            }
        }
    }

    private static func readInt32(_ data: Data, offset: Int) -> Int32 {
        data.withUnsafeBytes {
            $0.loadUnaligned(fromByteOffset: offset, as: Int32.self).bigEndian
        }
    }
}
