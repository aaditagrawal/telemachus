import Foundation
import Network

/// Minimal TCP server that matches SideScreen's protocol exactly
/// Protocol:
///   Display config: [type=1][width:4B BE][height:4B BE][rotation:4B BE]
///   Video frame:    [type=0][size:4B BE][H.265 data]
///   Video metadata: [type=6][size:4B BE][flags:1B][timestamp:8B BE][H.265 data]
///   Client opt-in:  [type=8]
class TestServer {
    private let port: UInt16
    private var listener: NWListener?
    private var connection: NWConnection?
    private let networkQueue = DispatchQueue(label: "testserver.network", qos: .userInteractive)
    private let sendQueue = DispatchQueue(label: "testserver.send", qos: .userInteractive)
    var onClientConnected: (() -> Void)?
    var onClientDisconnected: (() -> Void)?

    private(set) var isClientConnected = false
    private var framesSent: UInt64 = 0
    private var bytesSent: UInt64 = 0
    private var framesDropped: UInt64 = 0
    private var canSendNext = true
    private var isReceiving = false
    private var clientSupportsFrameMetadata = false
    private var clientConnectedCallbackSent = false
    private var inputBuffer = Data()

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
        isReceiving = false
        clientSupportsFrameMetadata = false
        clientConnectedCallbackSent = false
        inputBuffer.removeAll(keepingCapacity: true)

        newConnection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                print("[OK] Client connected!")
                self?.isClientConnected = true
                self?.startReceivingInput(on: newConnection)
                self?.networkQueue.asyncAfter(deadline: .now() + .milliseconds(100)) { [weak self, weak newConnection] in
                    guard let self = self, let newConnection = newConnection else { return }
                    self.finishClientStartup(on: newConnection)
                }
            case .failed, .cancelled:
                print("[INFO] Client disconnected")
                self?.isClientConnected = false
                self?.clientConnectedCallbackSent = false
                self?.isReceiving = false
                self?.inputBuffer.removeAll(keepingCapacity: true)
                self?.onClientDisconnected?()
            default: break
            }
        }
        newConnection.start(queue: networkQueue)
    }

    private func finishClientStartup(on conn: NWConnection) {
        guard connection === conn, isClientConnected, !clientConnectedCallbackSent else { return }
        print("[OK] Frame metadata: \(clientSupportsFrameMetadata ? "enabled" : "legacy")")
        onClientConnected?()
        clientConnectedCallbackSent = true
    }

    private func startReceivingInput(on conn: NWConnection) {
        guard !isReceiving else { return }
        isReceiving = true
        receiveInput(on: conn)
    }

    private func receiveInput(on conn: NWConnection) {
        guard connection === conn, isReceiving else { return }
        conn.receive(minimumIncompleteLength: 1, maximumLength: 256) { [weak self, weak conn] data, _, isComplete, error in
            guard let self = self, let conn = conn, self.connection === conn, self.isReceiving else { return }
            if error != nil || isComplete {
                self.isReceiving = false
                self.inputBuffer.removeAll(keepingCapacity: true)
                return
            }
            if let data = data, !data.isEmpty {
                self.inputBuffer.append(data)
                self.processInputBuffer(connection: conn)
            }
            self.receiveInput(on: conn)
        }
    }

    private func processInputBuffer(connection: NWConnection) {
        while let msgType = inputBuffer.first {
            switch msgType {
            case 2:
                guard inputBuffer.count >= 2 else { return }
                let pointerCount = Int(inputByte(at: 1))
                guard pointerCount == 1 || pointerCount == 2 else {
                    consumeInputBytes(1)
                    continue
                }
                let expectedSize = 2 + pointerCount * 8 + 4
                guard inputBuffer.count >= expectedSize else { return }
                consumeInputBytes(expectedSize)

            case 4:
                guard inputBuffer.count >= 9 else { return }
                let clientTimestamp = Data(inputBuffer.dropFirst().prefix(8))
                consumeInputBytes(9)
                var pong = Data(capacity: 9)
                pong.append(5)
                pong.append(clientTimestamp)
                connection.send(content: pong, completion: .contentProcessed { _ in })

            case 7:
                guard inputBuffer.count >= 2 else { return }
                consumeInputBytes(2)

            case 8:
                consumeInputBytes(1)
                if !clientSupportsFrameMetadata {
                    clientSupportsFrameMetadata = true
                    print("[OK] Client supports frame metadata")
                }
                finishClientStartup(on: connection)

            default:
                consumeInputBytes(1)
            }
        }
    }

    private func inputByte(at offset: Int) -> UInt8 {
        inputBuffer[inputBuffer.index(inputBuffer.startIndex, offsetBy: offset)]
    }

    private func consumeInputBytes(_ count: Int) {
        let endIndex = inputBuffer.index(inputBuffer.startIndex, offsetBy: count)
        inputBuffer.removeSubrange(inputBuffer.startIndex..<endIndex)
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

    /// Send a video frame (same protocol as SideScreen)
    func sendFrame(_ data: Data, isKeyframe: Bool) {
        guard let connection = connection, isClientConnected, clientConnectedCallbackSent else { return }

        sendQueue.async { [weak self] in
            guard let self = self else { return }
            guard self.isClientConnected, self.clientConnectedCallbackSent else { return }

            // Simple backpressure - but NEVER drop keyframes
            if !isKeyframe && !self.canSendNext {
                self.framesDropped += 1
                return
            }

            let packet: Data
            if self.clientSupportsFrameMetadata {
                var metadataPacket = Data(capacity: data.count + 14)
                metadataPacket.append(6)  // type = video frame with metadata
                var frameSize = Int32(data.count).bigEndian
                withUnsafeBytes(of: &frameSize) { metadataPacket.append(contentsOf: $0) }
                metadataPacket.append(isKeyframe ? 1 : 0)
                var timestamp = DispatchTime.now().uptimeNanoseconds.bigEndian
                withUnsafeBytes(of: &timestamp) { metadataPacket.append(contentsOf: $0) }
                metadataPacket.append(data)
                packet = metadataPacket
            } else {
                // TODO: Keep legacy frame type 0 for clients that do not advertise
                // metadata support; remove after legacy clients age out.
                var legacyPacket = Data(capacity: data.count + 5)
                legacyPacket.append(0)  // type = legacy video frame
                var frameSize = Int32(data.count).bigEndian
                withUnsafeBytes(of: &frameSize) { legacyPacket.append(contentsOf: $0) }
                legacyPacket.append(data)
                packet = legacyPacket
            }

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
    }

    func stop() {
        isReceiving = false
        connection?.cancel()
        listener?.cancel()
    }
}
