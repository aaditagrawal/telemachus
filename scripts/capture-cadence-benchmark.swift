import CoreGraphics
import CoreMedia
import CoreVideo
import Foundation
@preconcurrency import ScreenCaptureKit

private struct CadenceSnapshot {
    let callbackTimes: [UInt64]
    let presentationTimes: [Double]
}

private final class CadenceCollector: NSObject, SCStreamOutput {
    private let lock = NSLock()
    private var callbackTimes: [UInt64] = []
    private var presentationTimes: [Double] = []

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .screen, CMSampleBufferIsValid(sampleBuffer) else { return }

        let callbackTime = DispatchTime.now().uptimeNanoseconds
        let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds

        lock.lock()
        callbackTimes.append(callbackTime)
        presentationTimes.append(presentationTime)
        lock.unlock()
    }

    func snapshot() -> CadenceSnapshot {
        lock.lock()
        defer { lock.unlock() }
        return CadenceSnapshot(
            callbackTimes: callbackTimes,
            presentationTimes: presentationTimes
        )
    }
}

private struct CadenceStats {
    let frames: Int
    let fps: Double
    let meanIntervalMs: Double
    let p95IntervalMs: Double
    let maxIntervalMs: Double
    let gapsOver20Ms: Int
    let gapsOver25Ms: Int
}

private func stats(for timestamps: [Double], duration: Double) -> CadenceStats {
    let intervals = zip(timestamps, timestamps.dropFirst())
        .map { ($1 - $0) * 1_000 }
        .filter { $0 > 0 && $0 < 1_000 }
    let sorted = intervals.sorted()
    let p95Index = max(0, min(sorted.count - 1, Int(Double(sorted.count) * 0.95)))

    return CadenceStats(
        frames: timestamps.count,
        fps: Double(timestamps.count) / duration,
        meanIntervalMs: intervals.isEmpty ? 0 : intervals.reduce(0, +) / Double(intervals.count),
        p95IntervalMs: sorted.isEmpty ? 0 : sorted[p95Index],
        maxIntervalMs: sorted.max() ?? 0,
        gapsOver20Ms: intervals.count(where: { $0 > 20 }),
        gapsOver25Ms: intervals.count(where: { $0 > 25 })
    )
}

private func benchmark(queueDepth: Int, duration: Double) async throws {
    let content = try await SCShareableContent.excludingDesktopWindows(
        false,
        onScreenWindowsOnly: false
    )
    let displayID = CGMainDisplayID()
    guard let display = content.displays.first(where: { $0.displayID == displayID }) else {
        throw NSError(
            domain: "CaptureCadenceBenchmark",
            code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Main display \(displayID) is not shareable"]
        )
    }

    let configuration = SCStreamConfiguration()
    configuration.width = 2_000
    configuration.height = 1_124
    configuration.minimumFrameInterval = CMTime(value: 1, timescale: 60)
    configuration.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarFullRange
    configuration.queueDepth = queueDepth
    configuration.showsCursor = true
    configuration.scalesToFit = true
    if #available(macOS 14.0, *) {
        configuration.preservesAspectRatio = true
    }

    let collector = CadenceCollector()
    let outputQueue = DispatchQueue(
        label: "dev.telemachus.capture-benchmark.depth-\(queueDepth)",
        qos: .userInteractive
    )
    let filter = SCContentFilter(display: display, excludingWindows: [])
    let stream = SCStream(filter: filter, configuration: configuration, delegate: nil)
    try stream.addStreamOutput(collector, type: .screen, sampleHandlerQueue: outputQueue)

    let start = DispatchTime.now().uptimeNanoseconds
    try await stream.startCapture()
    try await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
    try await stream.stopCapture()
    let elapsed = Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000_000

    let snapshot = collector.snapshot()
    let callbackSeconds = snapshot.callbackTimes.map { Double($0) / 1_000_000_000 }
    let callbackStats = stats(for: callbackSeconds, duration: elapsed)
    let presentationStats = stats(for: snapshot.presentationTimes, duration: elapsed)

    print(
        String(
            format: "depth=%d elapsed=%.3fs frames=%d callback=%.3ffps " +
                "callback_interval(mean/p95/max)=%.3f/%.3f/%.3fms callback_gaps(>20/>25)=%d/%d " +
                "pts_interval(mean/p95/max)=%.3f/%.3f/%.3fms pts_gaps(>20/>25)=%d/%d",
            queueDepth,
            elapsed,
            callbackStats.frames,
            callbackStats.fps,
            callbackStats.meanIntervalMs,
            callbackStats.p95IntervalMs,
            callbackStats.maxIntervalMs,
            callbackStats.gapsOver20Ms,
            callbackStats.gapsOver25Ms,
            presentationStats.meanIntervalMs,
            presentationStats.p95IntervalMs,
            presentationStats.maxIntervalMs,
            presentationStats.gapsOver20Ms,
            presentationStats.gapsOver25Ms
        )
    )
}

// CGDisplayStream is marked obsolete by current SDKs but remains the
// production fallback for unsupported/private virtual-display combinations.
// The wrapper script compiles this benchmark with a macOS 13 deployment target,
// matching MacHost, so the fallback can be measured on newer systems.
private func benchmarkCGDisplayStream(duration: Double) async throws {
    let lock = NSLock()
    var callbackTimes: [UInt64] = []
    let outputQueue = DispatchQueue(
        label: "dev.telemachus.capture-benchmark.cgdisplaystream",
        qos: .userInteractive
    )

    guard let stream = CGDisplayStream(
        dispatchQueueDisplay: CGMainDisplayID(),
        outputWidth: 2_000,
        outputHeight: 1_124,
        pixelFormat: Int32(kCVPixelFormatType_420YpCbCr8BiPlanarFullRange),
        properties: nil,
        queue: outputQueue,
        handler: { status, _, surface, _ in
            guard status == .frameComplete, surface != nil else { return }
            lock.withLock {
                callbackTimes.append(DispatchTime.now().uptimeNanoseconds)
            }
        }
    ) else {
        throw NSError(
            domain: "CaptureCadenceBenchmark",
            code: 2,
            userInfo: [NSLocalizedDescriptionKey: "CGDisplayStream could not be created"]
        )
    }

    let start = DispatchTime.now().uptimeNanoseconds
    guard stream.start() == .success else {
        throw NSError(
            domain: "CaptureCadenceBenchmark",
            code: 3,
            userInfo: [NSLocalizedDescriptionKey: "CGDisplayStream failed to start"]
        )
    }
    try await Task.sleep(nanoseconds: UInt64(duration * 1_000_000_000))
    stream.stop()
    let elapsed = Double(DispatchTime.now().uptimeNanoseconds - start) / 1_000_000_000

    let timestamps = lock.withLock {
        callbackTimes.map { Double($0) / 1_000_000_000 }
    }
    let cadence = stats(for: timestamps, duration: elapsed)
    print(
        String(
            format: "api=CGDisplayStream elapsed=%.3fs frames=%d callback=%.3ffps " +
                "callback_interval(mean/p95/max)=%.3f/%.3f/%.3fms callback_gaps(>20/>25)=%d/%d",
            elapsed,
            cadence.frames,
            cadence.fps,
            cadence.meanIntervalMs,
            cadence.p95IntervalMs,
            cadence.maxIntervalMs,
            cadence.gapsOver20Ms,
            cadence.gapsOver25Ms
        )
    )
}

let duration = Double(
    CommandLine.arguments
        .dropFirst()
        .first(where: { $0.hasPrefix("--duration=") })?
        .split(separator: "=")
        .last ?? "10"
) ?? 10

let depths = CommandLine.arguments
    .dropFirst()
    .first(where: { $0.hasPrefix("--depths=") })?
    .split(separator: "=")
    .last?
    .split(separator: ",")
    .compactMap { Int($0) } ?? [2, 3, 5, 8]

Task {
    do {
        print("display=\(CGMainDisplayID()) duration=\(duration)s depths=\(depths)")
        if !CommandLine.arguments.contains("--cg-only") {
            for depth in depths {
                try await benchmark(queueDepth: depth, duration: duration)
            }
        }
        if CommandLine.arguments.contains("--include-cg") ||
            CommandLine.arguments.contains("--cg-only") {
            try await benchmarkCGDisplayStream(duration: duration)
        }
        exit(EXIT_SUCCESS)
    } catch {
        fputs("benchmark failed: \(error.localizedDescription)\n", stderr)
        exit(EXIT_FAILURE)
    }
}

dispatchMain()
