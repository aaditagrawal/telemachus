import Foundation

/// One node from `tailscale status --json`, reduced to what the Wireless
/// section displays. Mirrors the Linux client's peer model
/// (`LinuxClient/telemachus_client/tailscale.py`).
struct TailscalePeer: Identifiable, Equatable {
    let name: String
    let ip: String
    let os: String
    let online: Bool

    /// Peer keys are unique, so the address (or name when addressless) is a
    /// stable SwiftUI identity across refreshes.
    var id: String { ip.isEmpty ? name : ip }
}

/// Reads peers and this node's own Tailscale address from the `tailscale`
/// CLI. Every entry point degrades to "unavailable" / `[]` instead of
/// throwing: the CLI may be missing, the daemon may be stopped or logged
/// out, or `status` may be denied to a non-operator user.
///
/// The CLI is invoked as plain `tailscale` (never `sudo`). On installs where
/// `status` only works for the daemon operator, fix with:
///
///     sudo tailscale set --operator="$USER"
enum TailscaleStatus {
    /// Where the CLI commonly lives on macOS. The App Store / standalone
    /// build ships it inside the app bundle and never puts it on `PATH`;
    /// Homebrew uses a prefix that GUI-launched apps do not inherit.
    private static let candidateBinaries = [
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "/opt/homebrew/bin/tailscale",
        "/usr/local/bin/tailscale",
        "/usr/bin/tailscale"
    ]

    /// True when `tailscale status --json` runs and exits successfully, i.e.
    /// the CLI is installed *and* the daemon is up and reachable.
    static func available() -> Bool {
        statusJSON() != nil
    }

    /// Peers reported by `tailscale status --json`, online first.
    /// Returns `[]` when the CLI is missing, stopped, or unparseable.
    static func peers() -> [TailscalePeer] {
        guard let json = statusJSON() else { return [] }
        return parsePeers(json: json)
    }

    /// This machine's own Tailscale IPv4 (first IPv4 in `Self.TailscaleIPs`),
    /// used to build a Tailscale-reachable pairing URL. `nil` when Tailscale
    /// is unavailable or has no IPv4 address.
    static func tailscaleIP() -> String? {
        guard let json = statusJSON() else { return nil }
        return parseTailscaleIP(json: json)
    }

    // MARK: - Parsing (exposed for tests)

    /// Parses the top-level `Peer` map into peers. The local node is not a
    /// connection target and is skipped (it lives under `Self`, not `Peer`).
    static func parsePeers(json: [String: Any]) -> [TailscalePeer] {
        guard let raw = json["Peer"] as? [String: Any] else { return [] }
        return raw.values
            .compactMap { $0 as? [String: Any] }
            .compactMap(peer(from:))
            .sorted { lhs, rhs in
                if lhs.online != rhs.online { return lhs.online && !rhs.online }
                return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
            }
    }

    /// Parses `Self.TailscaleIPs`, preferring the first IPv4 entry.
    static func parseTailscaleIP(json: [String: Any]) -> String? {
        guard let selfObj = json["Self"] as? [String: Any] else { return nil }
        let ips = (selfObj["TailscaleIPs"] as? [String]) ?? []
        return primaryIPv4(in: ips)
    }

    // MARK: - CLI

    private static func statusJSON() -> [String: Any]? {
        guard let binary = resolveBinary() else { return nil }
        guard let data = run(binary, arguments: ["status", "--json"]) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    /// Locates the `tailscale` executable: known install locations first, then
    /// `PATH` via `/usr/bin/which` (which inherits the app's environment).
    private static func resolveBinary() -> String? {
        let fm = FileManager.default
        for path in candidateBinaries where fm.isExecutableFile(atPath: path) {
            return path
        }
        guard let data = run("/usr/bin/which", arguments: ["tailscale"]),
              let path = String(data: data, encoding: .utf8)?
                  .trimmingCharacters(in: .whitespacesAndNewlines),
              !path.isEmpty,
              fm.isExecutableFile(atPath: path) else {
            return nil
        }
        return path
    }

    /// Runs a child process and returns its stdout, or `nil` on launch
    /// failure, non-zero exit, or timeout. stdout is drained concurrently so a
    /// large body cannot fill the pipe buffer and stall the child.
    private static func run(
        _ binary: String,
        arguments: [String],
        timeout: TimeInterval = 3
    ) -> Data? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: binary)
        process.arguments = arguments
        let stdout = Pipe()
        process.standardOutput = stdout
        // Discard stderr: `tailscale` writes "Tailscale is stopped." there and
        // we only care about the exit status.
        process.standardError = Pipe()

        let exited = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in exited.signal() }

        do {
            try process.run()
        } catch {
            return nil
        }

        let readDone = DispatchSemaphore(value: 0)
        var output = Data()
        DispatchQueue.global(qos: .utility).async {
            output = stdout.fileHandleForReading.readDataToEndOfFile()
            readDone.signal()
        }

        if exited.wait(timeout: .now() + timeout) == .timedOut {
            process.terminate()
            return nil
        }
        _ = readDone.wait(timeout: .now() + timeout)
        guard process.terminationStatus == 0 else { return nil }
        return output
    }

    // MARK: - Helpers

    private static func peer(from entry: [String: Any]) -> TailscalePeer? {
        let ips = (entry["TailscaleIPs"] as? [String]) ?? []
        let ip = primaryIPv4(in: ips) ?? ""
        let host = (entry["HostName"] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
        let dns = (entry["DNSName"] as? String)?
            .trimmingCharacters(in: CharacterSet(charactersIn: ".")) ?? ""
        var name = host
        if name.isEmpty, !dns.isEmpty {
            name = dns.split(separator: ".").first.map(String.init) ?? dns
        }
        if name.isEmpty { name = ip }
        guard !name.isEmpty || !ip.isEmpty else { return nil }
        return TailscalePeer(
            name: name.isEmpty ? ip : name,
            ip: ip,
            os: (entry["OS"] as? String) ?? "",
            online: (entry["Online"] as? Bool) ?? false
        )
    }

    /// First IPv4 address in a `TailscaleIPs` / `AllowedIPs` style list,
    /// tolerating `ip/prefix` entries. IPv6-only nodes yield `nil`.
    private static func primaryIPv4(in list: [String]) -> String? {
        for raw in list {
            let address = raw.split(separator: "/", maxSplits: 1).first.map(String.init) ?? raw
            if !address.isEmpty && !address.contains(":") {
                return address
            }
        }
        return nil
    }
}
