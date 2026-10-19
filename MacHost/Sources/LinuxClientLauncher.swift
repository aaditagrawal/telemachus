import Foundation

enum LinuxClientLauncherError: LocalizedError {
    case timeout
    case nonZeroExit(status: Int32, output: String)
    case launchFailed(String)

    var errorDescription: String? {
        switch self {
        case .timeout:
            return "SSH timed out before the remote command finished."
        case .nonZeroExit(let status, let output):
            let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { return "SSH exited with status \(status)." }
            return "SSH exited with status \(status): \(trimmed)"
        case .launchFailed(let message):
            return message
        }
    }
}

final class LinuxClientLauncher {
    static let defaultUnitName = "telemachus-linux.service"

    let sshPath: String
    let keyPath: String
    let knownHostsPath: String
    let timeout: TimeInterval
    private let fileManager: FileManager
    private let keygenPath: String

    private(set) var lastSSHArguments: [String] = []

    var targetPeerName: String = ""
    var targetPeerIP: String = ""
    var targetSSHUser: String = ""
    var targetUnitName: String = defaultUnitName

    init(
        sshPath: String = "/usr/bin/ssh",
        keyPath: String? = nil,
        knownHostsPath: String? = nil,
        timeout: TimeInterval = 15,
        fileManager: FileManager = .default,
        keygenPath: String = "/usr/bin/ssh-keygen"
    ) {
        self.sshPath = sshPath
        let base = fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent(".config/telemachus/ssh", isDirectory: true)
        self.keyPath = keyPath ?? base.appendingPathComponent("id_ed25519").path
        self.knownHostsPath = knownHostsPath ?? base.appendingPathComponent("known_hosts").path
        self.timeout = timeout
        self.fileManager = fileManager
        self.keygenPath = keygenPath
    }

    convenience init(
        peerName: String,
        peerIP: String,
        sshUser: String,
        unitName: String = defaultUnitName,
        sshPath: String = "/usr/bin/ssh",
        keyPath: String? = nil,
        knownHostsPath: String? = nil,
        timeout: TimeInterval = 15,
        fileManager: FileManager = .default,
        keygenPath: String = "/usr/bin/ssh-keygen"
    ) {
        self.init(sshPath: sshPath, keyPath: keyPath, knownHostsPath: knownHostsPath, timeout: timeout, fileManager: fileManager, keygenPath: keygenPath)
        self.targetPeerName = peerName
        self.targetPeerIP = peerIP
        self.targetSSHUser = sshUser
        self.targetUnitName = unitName
    }

    convenience init(
        peer: TailscalePeer,
        sshUser: String,
        unitName: String = defaultUnitName,
        sshPath: String = "/usr/bin/ssh",
        keyPath: String? = nil,
        knownHostsPath: String? = nil,
        timeout: TimeInterval = 15,
        fileManager: FileManager = .default,
        keygenPath: String = "/usr/bin/ssh-keygen"
    ) {
        self.init(peerName: peer.name, peerIP: peer.ip, sshUser: sshUser, unitName: unitName, sshPath: sshPath, keyPath: keyPath, knownHostsPath: knownHostsPath, timeout: timeout, fileManager: fileManager, keygenPath: keygenPath)
    }

    func sshArguments(peerName: String, peerIP: String, sshUser: String, unitName: String = defaultUnitName) -> [String] {
        buildArguments(ip: peerIP, sshUser: sshUser, unitName: unitName)
    }
    func sshArguments(name: String, ip: String, user: String, unit: String = defaultUnitName) -> [String] {
        buildArguments(ip: ip, sshUser: user, unitName: unit)
    }
    func sshArguments(targetName: String, targetIP: String, sshUser: String, unitName: String = defaultUnitName) -> [String] {
        buildArguments(ip: targetIP, sshUser: sshUser, unitName: unitName)
    }
    func sshArguments(peerIP: String, sshUser: String, unitName: String = defaultUnitName) -> [String] {
        buildArguments(ip: peerIP, sshUser: sshUser, unitName: unitName)
    }
    func arguments(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName) -> [String] {
        buildArguments(ip: peerIP, sshUser: sshUser, unitName: unitName)
    }
    func buildCommandArguments(ip: String, sshUser: String, unitName: String = defaultUnitName) -> [String] {
        buildArguments(ip: ip, sshUser: sshUser, unitName: unitName)
    }

    private func buildArguments(ip: String, sshUser: String, unitName: String) -> [String] {
        let expandedKeyPath = (keyPath as NSString).expandingTildeInPath
        let expandedKnownHostsPath = (knownHostsPath as NSString).expandingTildeInPath
        let sshDir = (expandedKnownHostsPath as NSString).deletingLastPathComponent
        try? fileManager.createDirectory(atPath: sshDir, withIntermediateDirectories: true)

        var args: [String] = []
        if fileManager.fileExists(atPath: expandedKeyPath) {
            args.append("-i"); args.append(expandedKeyPath)
        }
        args.append("-o"); args.append("UserKnownHostsFile=\(expandedKnownHostsPath)")
        args.append("-o"); args.append("StrictHostKeyChecking=accept-new")
        args.append("-o"); args.append("BatchMode=yes")
        args.append("-o"); args.append("ConnectTimeout=\(max(1, Int(timeout)))")
        args.append("\(sshUser)@\(ip)")
        args.append("systemctl --user start \(unitName)")
        return args
    }

    func launch(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func launch(name: String = "", ip: String, user: String, unit: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: ip, sshUser: user, unitName: unit, completion: completion)
    }
    func launch(targetName: String = "", targetIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: targetIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func start(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func start(name: String = "", ip: String, user: String, unit: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: ip, sshUser: user, unitName: unit, completion: completion)
    }
    func trigger(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func trigger(name: String = "", ip: String, user: String, unit: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: ip, sshUser: user, unitName: unit, completion: completion)
    }
    func run(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func launch(peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Bool) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName) { completion($0.isSuccess) }
    }
    func launch(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Bool, String?) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName) { result in
            switch result { case .success: completion(true, nil); case .failure(let e): completion(false, e.localizedDescription) }
        }
    }
    func launch(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Error?) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName) { result in
            switch result { case .success: completion(nil); case .failure(let e): completion(e) }
        }
    }
    func launch(peer: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peer.ip, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func start(peer: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peer.ip, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func trigger(peer: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peer.ip, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func run(peer: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peer.ip, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func launch(target: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: target.ip, sshUser: sshUser, unitName: unitName, completion: completion)
    }

    func launch(completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: targetPeerIP, sshUser: targetSSHUser, unitName: targetUnitName, completion: completion)
    }
    func start(completion: @escaping (Result<Void, Error>) -> Void) {
        launch(completion: completion)
    }
    func trigger(completion: @escaping (Result<Void, Error>) -> Void) {
        launch(completion: completion)
    }
    func run(completion: @escaping (Result<Void, Error>) -> Void) {
        launch(completion: completion)
    }

    func launch(peer: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Bool) -> Void) {
        runSSH(ip: peer.ip, sshUser: sshUser, unitName: unitName) { completion($0.isSuccess) }
    }
    func launch(peer: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Error?) -> Void) {
        runSSH(ip: peer.ip, sshUser: sshUser, unitName: unitName) { result in
            switch result { case .success: completion(nil); case .failure(let e): completion(e) }
        }
    }
    func launch(target: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Bool) -> Void) {
        launch(peer: target, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func launch(target: TailscalePeer, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Error?) -> Void) {
        launch(peer: target, sshUser: sshUser, unitName: unitName, completion: completion)
    }

    func startLinuxClient(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func triggerLinuxClient(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }
    func runLinuxClient(peerName: String = "", peerIP: String, sshUser: String, unitName: String = defaultUnitName, completion: @escaping (Result<Void, Error>) -> Void) {
        runSSH(ip: peerIP, sshUser: sshUser, unitName: unitName, completion: completion)
    }

    private func runSSH(ip: String, sshUser: String, unitName: String, completion: @escaping (Result<Void, Error>) -> Void) {
        let args = buildArguments(ip: ip, sshUser: sshUser, unitName: unitName)
        lastSSHArguments = args
        DispatchQueue.global(qos: .utility).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.sshPath)
            process.arguments = args
            let stdoutPipe = Pipe(); let stderrPipe = Pipe()
            process.standardOutput = stdoutPipe; process.standardError = stderrPipe
            do { try process.run() } catch {
                DispatchQueue.main.async { completion(.failure(LinuxClientLauncherError.launchFailed(error.localizedDescription))) }
                return
            }
            let exited = DispatchSemaphore(value: 0)
            process.terminationHandler = { _ in exited.signal() }
            if exited.wait(timeout: .now() + self.timeout) == .timedOut {
                process.terminate()
                DispatchQueue.main.async { completion(.failure(LinuxClientLauncherError.timeout)) }
                return
            }
            let outData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
            let errData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
            let output = (String(data: outData, encoding: .utf8) ?? "") + (String(data: errData, encoding: .utf8) ?? "")
            if process.terminationStatus == 0 {
                DispatchQueue.main.async { completion(.success(())) }
            } else {
                DispatchQueue.main.async { completion(.failure(LinuxClientLauncherError.nonZeroExit(status: process.terminationStatus, output: output))) }
            }
        }
    }

    func generateKey() throws -> String { try generateKey(unitName: LinuxClientLauncher.defaultUnitName) }
    func generateKey(unitName: String) throws -> String {
        let dir = (keyPath as NSString).deletingLastPathComponent
        try fileManager.createDirectory(atPath: dir, withIntermediateDirectories: true)
        try? fileManager.removeItem(atPath: keyPath)
        try? fileManager.removeItem(atPath: keyPath + ".pub")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: keygenPath)
        process.arguments = ["-t", "ed25519", "-f", keyPath, "-N", "", "-C", "telemachus"]
        let pipe = Pipe()
        process.standardOutput = pipe; process.standardError = pipe
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            throw LinuxClientLauncherError.launchFailed(String(data: data, encoding: .utf8) ?? "ssh-keygen failed")
        }
        let pubPath = keyPath + ".pub"
        let publicKey = (try? String(contentsOfFile: pubPath, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let line = "restrict,command=\"/usr/bin/systemctl --user start \(unitName)\" \(publicKey)"
        print(line)
        return line
    }
    func generateSSHKey() throws -> String { try generateKey() }
    func generateKeyPair() throws -> String { try generateKey() }
    func generateKeyPair(unitName: String) throws -> String { try generateKey(unitName: unitName) }

    func authorizedKeysLine(unitName: String = defaultUnitName) -> String? {
        let pubPath = keyPath + ".pub"
        guard let publicKey = try? String(contentsOfFile: pubPath, encoding: .utf8) else { return nil }
        let trimmed = publicKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return "restrict,command=\"/usr/bin/systemctl --user start \(unitName)\" \(trimmed)"
    }
    func printAuthorizedKeysLine(unitName: String = defaultUnitName) {
        if let line = authorizedKeysLine(unitName: unitName) { print(line) }
    }
    var publicKey: String? {
        guard let s = try? String(contentsOfFile: keyPath + ".pub", encoding: .utf8) else { return nil }
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    static func defaultKeyPath() -> String {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/telemachus/ssh/id_ed25519").path
    }
    static func defaultKnownHostsPath() -> String {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/telemachus/ssh/known_hosts").path
    }
}

private extension Result {
    var isSuccess: Bool {
        if case .success = self { return true }
        return false
    }
}
