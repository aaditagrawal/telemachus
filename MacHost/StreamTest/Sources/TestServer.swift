import Foundation
import Network

/// Minimal TCP server that matches Telemachus's protocol exactly
/// Protocol:
///   Display config: [type=1][width:4B BE][height:4B BE][rotation:4B BE]
///   Video metadata: [type=6][size:4B BE][flags:1B][timestamp:8B BE][H.265 data]
class TestServer {
    private let port: UInt16
    private var listener: NWListener?
    private var connection: NWConnection?
    private let networkQueue = DispatchQueue(label: "testserver.network", qos: .userInteractive)
    private let sendQueue = DispatchQueue(label: "testserver.send", qos: .userInteractive)
    var onClientConnected: (() -> Void)?
    var onClientDisconnected: (() -> Void)?
    var onKeyframeRequested: (() -> Void)?

    private(set) var isClientConnected = false
    private var framesSent: UInt64 = 0
    private var bytesSent: UInt64 = 0
    private var framesDropped: UInt64 = 0
    private var pingsAnswered: UInt64 = 0
    private var touchEventsReceived: UInt64 = 0
    private var canSendNext = true
    private var inputBuffer = Data()
    private var isReceiving = false

    init(port: UInt16) {
        self.port = port
    }

    func start() {
        do {
            let params = NWParameters.tcp
            params.allowLocalEndpointReuse = true
            if let tcp = params.defaultProtocolStack.transportProtocol as? NWProtocolTCP.Options {
                tcp.noDelay = true
                tcp.enableFastOpen = true
            }

            listener = try NWListener(using: params, on: NWEndpoint.Port(integerLiteral: port))
            listener?.newConnectionHandler = { [weak self] conn in
                self?.handleConnection(conn)
            }
            listener?.stateUpdateHandler = { state in
                if case .ready = state {
                    print("[OK] TCP server listening on port \(self.port)")
                    print("     Run: adb reverse tcp:\(self.port) tcp:\(self.port)")
                }
            }
            listener?.start(queue: networkQueue)
        } catch {
            print("[FAIL] Server start error: \(error)")
        }
    }

    private func handleConnection(_ newConnection: NWConnection) {
        if let old = connection {
            old.cancel()
        }
        connection = newConnection
        canSendNext = true
        framesSent = 0
        bytesSent = 0
        framesDropped = 0
        pingsAnswered = 0
        touchEventsReceived = 0
        inputBuffer.removeAll(keepingCapacity: true)
        isReceiving = false
        isClientConnected = false

        newConnection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                print("[OK] Client connected!")
                self?.finishClientStartup(on: newConnection)
            case .failed, .cancelled:
                print("[INFO] Client disconnected")
                self?.isClientConnected = false
                self?.isReceiving = false
                self?.onClientDisconnected?()
            default: break
            }
        }
        newConnection.start(queue: networkQueue)
    }

    private func finishClientStartup(on conn: NWConnection) {
        guard connection === conn, !isClientConnected else { return }
        print("[OK] Frame metadata: enabled")
        isReceiving = true
        receiveClientInput(on: conn)
        onClientConnected?()
        isClientConnected = true
    }

    private func receiveClientInput(on conn: NWConnection) {
        guard connection === conn, isReceiving else { return }
        conn.receive(minimumIncompleteLength: 1, maximumLength: 256) { [weak self] data, _, complete, error in
            guard let self, self.connection === conn, self.isReceiving else { return }
            if let data, !data.isEmpty {
                self.inputBuffer.append(data)
                self.processClientInput(on: conn)
            }
            if complete || error != nil {
                self.isReceiving = false
                return
            }
            self.receiveClientInput(on: conn)
        }
    }

    private func processClientInput(on conn: NWConnection) {
        while let type = inputBuffer.first {
            switch type {
            case 2:
                guard inputBuffer.count >= 2 else { return }
                let pointerCount = Int(inputBuffer[inputBuffer.startIndex + 1])
                guard pointerCount == 1 || pointerCount == 2 else {
                    inputBuffer.removeFirst()
                    continue
                }
                let messageSize = 2 + pointerCount * 8 + 4
                guard inputBuffer.count >= messageSize else { return }
                inputBuffer.removeFirst(messageSize)
                touchEventsReceived += 1
                if touchEventsReceived <= 3 {
                    print("[INPUT] Touch packet received (pointers=\(pointerCount))")
                }

            case 4:
                guard inputBuffer.count >= 9 else { return }
                let timestamp = Data(inputBuffer.dropFirst().prefix(8))
                inputBuffer.removeFirst(9)
                var pong = Data([5])
                pong.append(timestamp)
                conn.send(content: pong, completion: .contentProcessed { _ in })
                pingsAnswered += 1

            case 7:
                guard inputBuffer.count >= 2 else { return }
                inputBuffer.removeFirst(2)
                onKeyframeRequested?()

            case 8:
                inputBuffer.removeFirst()
                print("[OK] Client advertised frame metadata support")

            case 9:
                inputBuffer.removeFirst()
                print("[INFO] Client requested AVC fallback (test stream remains HEVC)")

            default:
                print("[WARN] Unknown client input type \(type); discarding one byte")
                inputBuffer.removeFirst()
            }
        }
    }

    /// Send display size config (must be sent before frames)
    func sendDisplaySize(width: Int, height: Int, rotation: Int = 0) {
        guard let connection = connection else { return }
        var data = Data()
        data.append(1)  // type = display config
        data.append(contentsOf: withUnsafeBytes(of: Int32(width).bigEndian) { Data($0) })
        data.append(contentsOf: withUnsafeBytes(of: Int32(height).bigEndian) { Data($0) })
        data.append(contentsOf: withUnsafeBytes(of: Int32(rotation).bigEndian) { Data($0) })
        connection.send(content: data, completion: .contentProcessed { _ in })
        print("[OK] Sent display config: \(width)x\(height) @ \(rotation) deg")
    }

    /// Send a video frame (same protocol as Telemachus)
    func sendFrame(_ data: Data, isKeyframe: Bool) {
        guard let connection = connection, isClientConnected else { return }

        sendQueue.async { [weak self] in
            guard let self = self else { return }
            guard self.isClientConnected else { return }

            // Simple backpressure - but NEVER drop keyframes
            if !isKeyframe && !self.canSendNext {
                self.framesDropped += 1
                return
            }

            let packet = self.makeFramePacket(data, isKeyframe: isKeyframe)

            self.canSendNext = false
            connection.send(content: packet, completion: .contentProcessed { [weak self] error in
                self?.sendQueue.async {
                    self?.canSendNext = true
                }
                if error != nil {
                    self?.framesDropped += 1
                }
            })

            self.framesSent += 1
            self.bytesSent += UInt64(data.count)
        }
    }

    func printStats() {
        print("  Frames sent: \(framesSent), dropped: \(framesDropped), bytes: \(bytesSent / 1024)KB")
        print("  Pings answered: \(pingsAnswered), touch packets: \(touchEventsReceived)")
    }

    func stop() {
        connection?.cancel()
        listener?.cancel()
    }

    private func makeFramePacket(_ data: Data, isKeyframe: Bool) -> Data {
        var packet = Data(capacity: data.count + 14)
        packet.append(6)  // type = video frame with metadata
        appendFrameSize(data.count, to: &packet)
        packet.append(isKeyframe ? 1 : 0)
        var timestamp = DispatchTime.now().uptimeNanoseconds.bigEndian
        withUnsafeBytes(of: &timestamp) { packet.append(contentsOf: $0) }
        packet.append(data)
        return packet
    }

    private func appendFrameSize(_ size: Int, to packet: inout Data) {
        var frameSize = Int32(size).bigEndian
        withUnsafeBytes(of: &frameSize) { packet.append(contentsOf: $0) }
    }
}
