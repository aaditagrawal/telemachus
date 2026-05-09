import Foundation

enum HandshakeError: Error, Equatable {
    case invalidMagic
    case invalidName
    case truncated
}

enum HandshakeStatus: UInt8 {
    case ok = 0x00
    case invalidToken = 0x01
    case invalidMagic = 0x02
    case invalidName = 0x03
}

struct ParsedHandshake {
    let token: Data
    let deviceName: String
}

enum HandshakeCodec {
    static let requestMagic: [UInt8] = [0x53, 0x53, 0x57, 0x41]   // "SSWA"
    static let responseMagic: [UInt8] = [0x53, 0x53, 0x57, 0x52]  // "SSWR"
    static let fixedPrefixLen = 4 + 32 + 1                         // magic + token + name_len

    /// Parses the variable-length request `[magic 4][token 32][name_len 1][name N]`.
    static func parseRequest(_ data: Data) throws -> ParsedHandshake {
        guard data.count >= fixedPrefixLen else { throw HandshakeError.truncated }
        let bytes = Array(data)
        guard Array(bytes[0..<4]) == requestMagic else { throw HandshakeError.invalidMagic }
        let token = Data(bytes[4..<36])
        let nameLen = Int(bytes[36])
        guard nameLen >= 1 && nameLen <= 64 else { throw HandshakeError.invalidName }
        guard data.count >= fixedPrefixLen + nameLen else { throw HandshakeError.truncated }
        let nameBytes = Array(bytes[37..<(37 + nameLen)])
        guard let name = String(bytes: nameBytes, encoding: .utf8), !name.isEmpty else {
            throw HandshakeError.invalidName
        }
        return ParsedHandshake(token: token, deviceName: name)
    }

    static func encodeResponse(status: HandshakeStatus) -> Data {
        Data(responseMagic + [status.rawValue])
    }
}
