import Foundation

struct PairedDevice: Codable, Equatable {
    let name: String
    let lastConnected: Date
}

final class PairedDeviceStore {
    static let userDefaultsKey = "wireless.pairedDevices"

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func all() -> [PairedDevice] {
        guard let data = defaults.data(forKey: Self.userDefaultsKey),
              let decoded = try? JSONDecoder().decode([PairedDevice].self, from: data) else {
            return []
        }
        return decoded.sorted { $0.lastConnected > $1.lastConnected }
    }

    func upsert(name: String, lastConnected: Date) {
        var current = all()
        current.removeAll { $0.name == name }
        current.append(PairedDevice(name: name, lastConnected: lastConnected))
        save(current)
    }

    func forget(name: String) {
        var current = all()
        current.removeAll { $0.name == name }
        save(current)
    }

    func clear() {
        defaults.removeObject(forKey: Self.userDefaultsKey)
    }

    private func save(_ devices: [PairedDevice]) {
        let encoded = (try? JSONEncoder().encode(devices)) ?? Data()
        defaults.set(encoded, forKey: Self.userDefaultsKey)
    }
}
