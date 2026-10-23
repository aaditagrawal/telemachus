import Foundation
import Darwin

enum LinuxClientLauncherError: LocalizedError {
    case timeout
    case nonZeroExit(status: Int32, output: String)
    case launchFailed(String)

    var errorDescription: String? {
        switch self {
        case .timeout:
            return "SSH command timed out."
        case .nonZeroExit(let status, let output):
            let trimmed = output.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty { return "SSH command exited with status \(status)." }
            return "SSH command exited with status \(status): \(trimmed)"
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
        self.keyPath = ((keyPath ?? base.appendingPathComponent("id_ed25519").path) as NSString)
            .expandingTildeInPath
        self.knownHostsPath = ((knownHostsPath ?? base.appendingPathComponent("known_hosts").path) as NSString)
            .expandingTildeInPath
        self.timeout = timeout
        self.fileManager = fileManager
        self.keygenPath = keygenPath
    }

    func sshArguments(
        peerIP: String,
        sshUser: String,
        unitName: String = defaultUnitName
    ) -> [String] {
        [
            "-i", keyPath,
            "-o", "IdentitiesOnly=yes",
            "-o", "IdentityAgent=none",
            "-o", "UserKnownHostsFile=\(knownHostsPath)",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=\(max(1, Int(timeout)))",
            "--", "\(sshUser)@\(peerIP)",
            "systemctl --user start -- \(Self.shellQuote(unitName))"
        ]
    }

    func launch(
        peerName: String = "",
        peerIP: String,
        sshUser: String,
        unitName: String = defaultUnitName,
        completion: @escaping (Result<Void, Error>) -> Void
    ) {
        let arguments = sshArguments(peerIP: peerIP, sshUser: sshUser, unitName: unitName)
        DispatchQueue.global(qos: .utility).async {
            let result = Result<Void, Error> {
                _ = try self.generateKey(unitName: unitName)
                try self.fileManager.createDirectory(
                    atPath: (self.knownHostsPath as NSString).deletingLastPathComponent,
                    withIntermediateDirectories: true,
                    attributes: [.posixPermissions: 0o700]
                )
                _ = try self.runProcess(self.sshPath, arguments: arguments)
            }
            DispatchQueue.main.async { completion(result) }
        }
    }

    /// Reuse the dedicated key. If only its public half is missing, derive it
    /// from the private key so previously installed authorized_keys still work.
    @discardableResult
    func generateKey(unitName: String = defaultUnitName) throws -> String {
        let directory = (keyPath as NSString).deletingLastPathComponent
        try fileManager.createDirectory(
            atPath: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )

        if fileManager.fileExists(atPath: keyPath) {
            if authorizedKeysLine(unitName: unitName) == nil {
                let publicKey = try runProcess(keygenPath, arguments: ["-y", "-P", "", "-f", keyPath])
                guard !publicKey.isEmpty else {
                    throw LinuxClientLauncherError.launchFailed("ssh-keygen returned an empty public key.")
                }
                try publicKey.write(to: URL(fileURLWithPath: keyPath + ".pub"), options: .atomic)
            }
        } else {
            guard !fileManager.fileExists(atPath: keyPath + ".pub") else {
                throw LinuxClientLauncherError.launchFailed(
                    "The dedicated SSH private key is missing. Restore it before starting the Linux client."
                )
            }
            _ = try runProcess(
                keygenPath,
                arguments: ["-t", "ed25519", "-f", keyPath, "-N", "", "-C", "telemachus"]
            )
        }

        guard let line = authorizedKeysLine(unitName: unitName) else {
            throw LinuxClientLauncherError.launchFailed("Could not read the dedicated SSH public key.")
        }
        return line
    }

    func authorizedKeysLine(unitName: String = defaultUnitName) -> String? {
        guard let publicKey = try? String(contentsOfFile: keyPath + ".pub", encoding: .utf8) else {
            return nil
        }
        let trimmed = publicKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let command = "/usr/bin/systemctl --user start -- \(Self.shellQuote(unitName))"
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        return "restrict,command=\"\(command)\" \(trimmed)"
    }

    private static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Drain combined stdout/stderr while the child runs. Waiting for exit
    /// before reading can deadlock on a full pipe. Install both handlers first
    /// so even a process that exits immediately is observed.
    private func runProcess(_ executable: String, arguments: [String]) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        let output = ProcessOutput()
        let readDone = DispatchSemaphore(value: 0)
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                readDone.signal()
            } else {
                output.append(data)
            }
        }
        let exited = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in exited.signal() }
        defer {
            pipe.fileHandleForReading.readabilityHandler = nil
            try? pipe.fileHandleForReading.close()
            try? pipe.fileHandleForWriting.close()
        }

        try process.run()
        try? pipe.fileHandleForWriting.close()
        let deadline = DispatchTime.now() + timeout
        if exited.wait(timeout: deadline) == .timedOut {
            if process.isRunning { process.terminate() }
            if exited.wait(timeout: .now() + 1) == .timedOut, process.isRunning {
                kill(process.processIdentifier, SIGKILL)
                _ = exited.wait(timeout: .now() + 1)
            }
            throw LinuxClientLauncherError.timeout
        }
        guard readDone.wait(timeout: deadline) == .success else {
            throw LinuxClientLauncherError.timeout
        }
        let data = output.data
        guard process.terminationStatus == 0 else {
            throw LinuxClientLauncherError.nonZeroExit(
                status: process.terminationStatus,
                output: String(data: data, encoding: .utf8) ?? "SSH output is not valid UTF-8."
            )
        }
        return data
    }
}

private final class ProcessOutput {
    private let lock = NSLock()
    private var storage = Data()

    func append(_ data: Data) {
        lock.withLock { storage.append(data) }
    }

    var data: Data {
        lock.withLock { storage }
    }
}
