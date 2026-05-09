import Foundation
import Security

enum WirelessAuth {
    static let userDefaultsKey = "wireless.authToken"

    static func generateToken() -> Data {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, 32, &bytes)
        precondition(status == errSecSuccess, "SecRandomCopyBytes failed: \(status)")
        return Data(bytes)
    }

    static func persist(_ token: Data, defaults: UserDefaults = .standard) {
        defaults.set(token, forKey: userDefaultsKey)
    }

    static func load(defaults: UserDefaults = .standard) -> Data? {
        defaults.data(forKey: userDefaultsKey)
    }

    static func loadOrCreate(defaults: UserDefaults = .standard) -> Data {
        if let existing = load(defaults: defaults), existing.count == 32 {
            return existing
        }
        let fresh = generateToken()
        persist(fresh, defaults: defaults)
        return fresh
    }

    @discardableResult
    static func reset(defaults: UserDefaults = .standard) -> Data {
        defaults.removeObject(forKey: userDefaultsKey)
        return loadOrCreate(defaults: defaults)
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
