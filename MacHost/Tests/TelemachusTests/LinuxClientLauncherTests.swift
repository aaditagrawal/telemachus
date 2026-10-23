import XCTest
import Darwin
@testable import Telemachus

final class LinuxClientLauncherTests: XCTestCase {
    private var directory = URL(fileURLWithPath: "/unused")

    override func setUpWithError() throws {
        try super.setUpWithError()
        directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("Telemachus.LauncherTests.\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try FileManager.default.removeItem(at: directory)
        try super.tearDownWithError()
    }

    func testArgumentsAlwaysUseDedicatedIdentityAndKnownHosts() {
        let client = launcher()
        XCTAssertFalse(FileManager.default.fileExists(atPath: client.keyPath))
        XCTAssertEqual(client.sshArguments(peerIP: "100.64.0.2", sshUser: "receiver"), [
            "-i", client.keyPath,
            "-o", "IdentitiesOnly=yes",
            "-o", "IdentityAgent=none",
            "-o", "UserKnownHostsFile=\(client.knownHostsPath)",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=3",
            "--", "receiver@100.64.0.2",
            "systemctl --user start -- 'telemachus-linux.service'"
        ])
    }

    func testRemoteUnitIsOneShellArgument() {
        let arguments = launcher().sshArguments(
            peerIP: "100.64.0.2", sshUser: "receiver", unitName: "test'; echo unsafe"
        )
        XCTAssertEqual(arguments.last, "systemctl --user start -- 'test'\\''; echo unsafe'")
    }

    func testKeyGenerationReusesExistingPairWithoutRunningKeygen() throws {
        let client = launcher()
        let line = try client.generateKey()
        let privateKey = try Data(contentsOf: URL(fileURLWithPath: client.keyPath))
        let publicKey = try Data(contentsOf: URL(fileURLWithPath: client.keyPath + ".pub"))

        let reuse = launcher(keygenPath: "/missing-keygen")
        XCTAssertEqual(try reuse.generateKey(), line)
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: client.keyPath)), privateKey)
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: client.keyPath + ".pub")), publicKey)
        XCTAssertTrue(line.hasPrefix("restrict,command=\"/usr/bin/systemctl --user start -- 'telemachus-linux.service'\" ssh-ed25519 "))
    }

    func testMissingPublicKeyIsRecoveredWithoutReplacingPrivateKey() throws {
        let client = launcher()
        try client.generateKey()
        let privateKey = try Data(contentsOf: URL(fileURLWithPath: client.keyPath))
        let publicKey = try String(contentsOfFile: client.keyPath + ".pub", encoding: .utf8)
        try FileManager.default.removeItem(atPath: client.keyPath + ".pub")

        try client.generateKey()

        let recovered = try String(contentsOfFile: client.keyPath + ".pub", encoding: .utf8)
        XCTAssertEqual(Array(recovered.split(whereSeparator: \.isWhitespace).prefix(2)),
                       Array(publicKey.split(whereSeparator: \.isWhitespace).prefix(2)))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: client.keyPath)), privateKey)
    }

    func testOrphanedPublicKeyIsPreservedAndReported() throws {
        let client = launcher(keygenPath: "/missing-keygen")
        let publicKey = "ssh-ed25519 existing-key receiver\n"
        try publicKey.write(toFile: client.keyPath + ".pub", atomically: true, encoding: .utf8)

        XCTAssertThrowsError(try client.generateKey())
        XCTAssertEqual(try String(contentsOfFile: client.keyPath + ".pub", encoding: .utf8), publicKey)
        XCTAssertFalse(FileManager.default.fileExists(atPath: client.keyPath))
    }

    func testFastExitCompletesOnceOnMainQueue() throws {
        try seedKeyPair()
        let completed = expectation(description: "immediate SSH success")
        completed.assertForOverFulfill = true
        launcher().launch(peerIP: "100.64.0.2", sshUser: "receiver") { result in
            XCTAssertTrue(Thread.isMainThread)
            if case .failure(let error) = result { XCTFail(error.localizedDescription) }
            completed.fulfill()
        }
        wait(for: [completed], timeout: 4)
    }

    func testLargeStdoutAndStderrAreDrainedAndFailureIsReported() throws {
        try seedKeyPair()
        let script = try writeScript("verbose-ssh", body: """
        dd if=/dev/zero bs=65536 count=4 2>/dev/null
        dd if=/dev/zero bs=65536 count=4 2>/dev/null | /bin/cat >&2
        printf 'remote-diagnostic' >&2
        exit 7
        """)
        let completed = expectation(description: "verbose SSH failure")
        launcher(sshPath: script).launch(peerIP: "100.64.0.2", sshUser: "receiver") { result in
            switch result {
            case .success:
                XCTFail("Expected remote failure")
            case .failure(let error):
                guard let failure = error as? LinuxClientLauncherError,
                      case .nonZeroExit(let status, let output) = failure else {
                    XCTFail("Unexpected failure: \(error)")
                    completed.fulfill()
                    return
                }
                XCTAssertEqual(status, 7)
                XCTAssertTrue(output.contains("remote-diagnostic"))
                XCTAssertGreaterThan(output.utf8.count, 500_000)
            }
            completed.fulfill()
        }
        wait(for: [completed], timeout: 5)
    }

    func testTimeoutKillsUncooperativeChildAndCompletesOnMainQueue() throws {
        try seedKeyPair()
        let pidURL = directory.appendingPathComponent("child.pid")
        let script = try writeScript("hanging-ssh", body: """
        echo $$ > '\(pidURL.path)'
        trap '' TERM
        while :; do :; done
        """)
        defer {
            if let text = try? String(contentsOf: pidURL, encoding: .utf8),
               let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)) {
                kill(pid, SIGKILL)
            }
        }
        let completed = expectation(description: "SSH timeout")
        launcher(sshPath: script, timeout: 0.2).launch(peerIP: "100.64.0.2", sshUser: "receiver") { result in
            XCTAssertTrue(Thread.isMainThread)
            switch result {
            case .success:
                XCTFail("Expected timeout")
            case .failure(let error):
                XCTAssertEqual(error.localizedDescription, LinuxClientLauncherError.timeout.localizedDescription)
            }
            completed.fulfill()
        }
        wait(for: [completed], timeout: 4)
        let pidText = try String(contentsOf: pidURL, encoding: .utf8)
        let pid = try XCTUnwrap(Int32(pidText.trimmingCharacters(in: .whitespacesAndNewlines)))
        let killResult = kill(pid, 0)
        let killError = errno
        XCTAssertEqual(killResult, -1, "Timed-out child must be reaped")
        XCTAssertEqual(killError, ESRCH)
    }

    func testLaunchFailureIsReturnedToCaller() throws {
        try seedKeyPair()
        let completed = expectation(description: "missing SSH executable")
        launcher(sshPath: "/missing-ssh").launch(peerIP: "100.64.0.2", sshUser: "receiver") { result in
            if case .success = result { XCTFail("Expected launch failure") }
            completed.fulfill()
        }
        wait(for: [completed], timeout: 4)
    }

    private func launcher(
        sshPath: String = "/usr/bin/true",
        timeout: TimeInterval = 3,
        keygenPath: String = "/usr/bin/ssh-keygen"
    ) -> LinuxClientLauncher {
        LinuxClientLauncher(
            sshPath: sshPath,
            keyPath: directory.appendingPathComponent("id_ed25519").path,
            knownHostsPath: directory.appendingPathComponent("known_hosts").path,
            timeout: timeout,
            keygenPath: keygenPath
        )
    }

    private func seedKeyPair() throws {
        let client = launcher()
        try Data("private-placeholder".utf8).write(to: URL(fileURLWithPath: client.keyPath))
        try "ssh-ed25519 public-placeholder receiver\n".write(
            toFile: client.keyPath + ".pub", atomically: true, encoding: .utf8
        )
    }

    private func writeScript(_ name: String, body: String) throws -> String {
        let url = directory.appendingPathComponent(name)
        try "#!/bin/sh\n\(body)\n".write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        return url.path
    }
}
