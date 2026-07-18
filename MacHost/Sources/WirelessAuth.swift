import Foundation
import Security

protocol WirelessAuthTokenStore {
    func load() throws -> Data?
    func persist(_ token: Data) throws
    func delete() throws
}

enum KeychainTokenStoreError: LocalizedError {
    case unexpectedStatus(OSStatus)
    case unexpectedValue

    var errorDescription: String? {
        switch self {
        case .unexpectedStatus(let status):
            let message = SecCopyErrorMessageString(status, nil) as String?
            return message ?? "Keychain operation failed with status \(status)."
        case .unexpectedValue:
            return "Keychain returned an unexpected token value."
        }
    }
}

struct KeychainWirelessAuthTokenStore: WirelessAuthTokenStore {
    static let account = "wireless-pairing-token"

    let service: String
    let account: String

    init(
        service: String = KeychainWirelessAuthTokenStore.defaultService,
        account: String = KeychainWirelessAuthTokenStore.account
    ) {
        self.service = service
        self.account = account
    }

    static var defaultService: String {
        let bundleID = Bundle.main.bundleIdentifier ?? "dev.telemachus.display"
        return "\(bundleID).wireless-auth"
    }

    func load() throws -> Data? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        switch status {
        case errSecSuccess:
            guard let data = result as? Data else {
                throw KeychainTokenStoreError.unexpectedValue
            }
            return data
        case errSecItemNotFound:
            return nil
        default:
            throw KeychainTokenStoreError.unexpectedStatus(status)
        }
    }

    func persist(_ token: Data) throws {
        let attributes = [kSecValueData as String: token]
        let updateStatus = SecItemUpdate(
            baseQuery as CFDictionary,
            attributes as CFDictionary
        )
        switch updateStatus {
        case errSecSuccess:
            return
        case errSecItemNotFound:
            var item = baseQuery
            item[kSecValueData as String] = token
            item[kSecAttrAccessible as String] =
                kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let addStatus = SecItemAdd(item as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw KeychainTokenStoreError.unexpectedStatus(addStatus)
            }
        default:
            throw KeychainTokenStoreError.unexpectedStatus(updateStatus)
        }
    }

    func delete() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainTokenStoreError.unexpectedStatus(status)
        }
    }

    private var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kCFBooleanFalse as Any
        ]
    }
}

enum WirelessAuth {
    /// Legacy preference key retained only for one-time Keychain migration.
    static let userDefaultsKey = "wireless.authToken"

    static func generateToken() -> Data {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, 32, &bytes)
        precondition(status == errSecSuccess, "SecRandomCopyBytes failed: \(status)")
        return Data(bytes)
    }

    static func persist(
        _ token: Data,
        store: any WirelessAuthTokenStore = KeychainWirelessAuthTokenStore(),
        legacyDefaults: UserDefaults = .standard
    ) throws {
        guard token.count == 32 else {
            throw KeychainTokenStoreError.unexpectedValue
        }
        try store.persist(token)
        legacyDefaults.removeObject(forKey: userDefaultsKey)
    }

    /// Loads the Keychain token. If none exists, a valid token from the old
    /// UserDefaults location is migrated exactly once and then erased.
    static func load(
        store: any WirelessAuthTokenStore = KeychainWirelessAuthTokenStore(),
        legacyDefaults: UserDefaults = .standard
    ) throws -> Data? {
        if let keychainToken = try store.load() {
            legacyDefaults.removeObject(forKey: userDefaultsKey)
            return keychainToken.count == 32 ? keychainToken : nil
        }

        guard let legacyToken = legacyDefaults.data(forKey: userDefaultsKey) else {
            return nil
        }
        guard legacyToken.count == 32 else {
            legacyDefaults.removeObject(forKey: userDefaultsKey)
            return nil
        }

        // Erase the legacy copy only after the Keychain write succeeds.
        try store.persist(legacyToken)
        legacyDefaults.removeObject(forKey: userDefaultsKey)
        return legacyToken
    }

    static func loadOrCreate(
        store: any WirelessAuthTokenStore = KeychainWirelessAuthTokenStore(),
        legacyDefaults: UserDefaults = .standard
    ) throws -> Data {
        if let existing = try load(
            store: store,
            legacyDefaults: legacyDefaults
        ) {
            return existing
        }
        let fresh = generateToken()
        try persist(
            fresh,
            store: store,
            legacyDefaults: legacyDefaults
        )
        return fresh
    }

    @discardableResult
    static func reset(
        store: any WirelessAuthTokenStore = KeychainWirelessAuthTokenStore(),
        legacyDefaults: UserDefaults = .standard
    ) throws -> Data {
        let fresh = generateToken()
        try persist(
            fresh,
            store: store,
            legacyDefaults: legacyDefaults
        )
        return fresh
    }

    static func validate(_ candidate: Data, expected: Data) -> Bool {
        guard candidate.count == expected.count else { return false }
        var diff: UInt8 = 0
        for i in 0..<expected.count {
            diff |= candidate[i] ^ expected[i]
        }
        return diff == 0
    }
}
