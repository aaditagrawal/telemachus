import Foundation
import CoreImage
import AppKit

enum QRRenderer {
    /// Renders the given pairing URL as a QR code NSImage of the requested side length (in points).
    /// Returns nil if encoding fails.
    static func render(url: String, size: CGFloat = 220) -> NSImage? {
        guard let data = url.data(using: .utf8) else { return nil }
        guard let filter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        filter.setValue(data, forKey: "inputMessage")
        filter.setValue("M", forKey: "inputCorrectionLevel")
        guard let ciImage = filter.outputImage else { return nil }

        let scale = size / ciImage.extent.width
        let scaled = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))

        let rep = NSCIImageRep(ciImage: scaled)
        let nsImage = NSImage(size: rep.size)
        nsImage.addRepresentation(rep)
        return nsImage
    }
}
