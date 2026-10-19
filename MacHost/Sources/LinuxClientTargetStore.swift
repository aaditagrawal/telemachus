import Foundation

/// Persists the selected Linux client target (peer name/IP, SSH user) and the
/// last trigger result in UserDefaults.
final class LinuxClientTargetStore {
    private let defaults: UserDefaults

    // Canonical backing keys.
    private let peerNameKey: String
    private let peerIPKey: String
    private let sshUserKey: String
    private let lastResultKey: String

    init(
        defaults: UserDefaults = .standard,
        peerNameKey: String = "Telemachus_linuxTargetPeerName",
        peerIPKey: String = "Telemachus_linuxTargetPeerIP",
        sshUserKey: String = "Telemachus_linuxSSHUser",
        lastResultKey: String = "Telemachus_linuxTriggerStatus"
    ) {
        self.defaults = defaults
        self.peerNameKey = peerNameKey
        self.peerIPKey = peerIPKey
        self.sshUserKey = sshUserKey
        self.lastResultKey = lastResultKey
    }

    // MARK: - Canonical properties

    var selectedPeerName: String? {
        get { defaults.string(forKey: peerNameKey) }
        set {
            if let newValue { defaults.set(newValue, forKey: peerNameKey) }
            else { defaults.removeObject(forKey: peerNameKey) }
        }
    }

    var selectedPeerIP: String? {
        get { defaults.string(forKey: peerIPKey) }
        set {
            if let newValue { defaults.set(newValue, forKey: peerIPKey) }
            else { defaults.removeObject(forKey: peerIPKey) }
        }
    }

    var sshUser: String {
        get { defaults.string(forKey: sshUserKey) ?? "aditya" }
        set { defaults.set(newValue, forKey: sshUserKey) }
    }

    var lastResult: String? {
        get { defaults.string(forKey: lastResultKey) }
        set {
            if let newValue { defaults.set(newValue, forKey: lastResultKey) }
            else { defaults.removeObject(forKey: lastResultKey) }
        }
    }

    // MARK: - Aliases (peer name)

    var peerName: String? { get { selectedPeerName } set { selectedPeerName = newValue } }
    var targetPeerName: String? { get { selectedPeerName } set { selectedPeerName = newValue } }
    var peer: String? { get { selectedPeerName } set { selectedPeerName = newValue } }
    var targetName: String? { get { selectedPeerName } set { selectedPeerName = newValue } }
    var name: String? { get { selectedPeerName } set { selectedPeerName = newValue } }

    // MARK: - Aliases (peer IP)

    var peerIP: String? { get { selectedPeerIP } set { selectedPeerIP = newValue } }
    var targetPeerIP: String? { get { selectedPeerIP } set { selectedPeerIP = newValue } }
    var ip: String? { get { selectedPeerIP } set { selectedPeerIP = newValue } }
    var targetIP: String? { get { selectedPeerIP } set { selectedPeerIP = newValue } }
    var address: String? { get { selectedPeerIP } set { selectedPeerIP = newValue } }

    // MARK: - Aliases (ssh user)

    var username: String { get { sshUser } set { sshUser = newValue } }
    var user: String { get { sshUser } set { sshUser = newValue } }
    var sshUsername: String { get { sshUser } set { sshUser = newValue } }

    // MARK: - Aliases (last result)

    var lastTriggerResult: String? { get { lastResult } set { lastResult = newValue } }
    var lastStatus: String? { get { lastResult } set { lastResult = newValue } }
    var lastOutcome: String? { get { lastResult } set { lastResult = newValue } }
    var triggerStatus: String? { get { lastResult } set { lastResult = newValue } }
    var status: String? { get { lastResult } set { lastResult = newValue } }

    // MARK: - Helpers

    var selectedPeer: (name: String, ip: String)? {
        get {
            guard let n = selectedPeerName, let i = selectedPeerIP else { return nil }
            return (n, i)
        }
        set {
            selectedPeerName = newValue?.name
            selectedPeerIP = newValue?.ip
        }
    }

    func select(peerName: String, peerIP: String, sshUser: String? = nil) {
        selectedPeerName = peerName
        selectedPeerIP = peerIP
        if let sshUser { self.sshUser = sshUser }
    }

    func select(peer: TailscalePeer, sshUser: String? = nil) {
        select(peerName: peer.name, peerIP: peer.ip, sshUser: sshUser)
    }

    func recordResult(_ result: String) {
        lastResult = result
    }

    func clear() {
        selectedPeerName = nil
        selectedPeerIP = nil
        lastResult = nil
    }
}
