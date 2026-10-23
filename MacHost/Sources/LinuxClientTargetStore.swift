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
            if let newValue {
                defaults.set(newValue, forKey: peerNameKey)
            } else {
                defaults.removeObject(forKey: peerNameKey)
            }
        }
    }

    var selectedPeerIP: String? {
        get { defaults.string(forKey: peerIPKey) }
        set {
            if let newValue {
                defaults.set(newValue, forKey: peerIPKey)
            } else {
                defaults.removeObject(forKey: peerIPKey)
            }
        }
    }

    var sshUser: String {
        get { defaults.string(forKey: sshUserKey) ?? "" }
        set { defaults.set(newValue, forKey: sshUserKey) }
    }

    var lastResult: String? {
        get { defaults.string(forKey: lastResultKey) }
        set {
            if let newValue {
                defaults.set(newValue, forKey: lastResultKey)
            } else {
                defaults.removeObject(forKey: lastResultKey)
            }
        }
    }

    // MARK: - Helpers

    func select(peerName: String, peerIP: String, sshUser: String? = nil) {
        selectedPeerName = peerName
        selectedPeerIP = peerIP
        if let sshUser { self.sshUser = sshUser }
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
