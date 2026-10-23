import XCTest
import Combine
@testable import Telemachus

final class AppDelegateLifecycleTests: XCTestCase {
    private var suiteName = ""
    private var defaults = UserDefaults.standard

    override func setUp() {
        super.setUp()
        suiteName = "Telemachus.AppDelegateLifecycleTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)!
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    @MainActor
    func testCountAndClampedIndexProduceOneRestartWithCommittedValues() async {
        let delegate = RecordingAppDelegate()
        delegate.settings = DisplaySettings(defaults: defaults)
        delegate.settings.displaySource = .extended
        delegate.settings.virtualDisplayCount = 4
        delegate.settings.streamedDisplayIndex = 3
        delegate.settings.isRunning = true
        delegate.setupSettingsObservers()
        let restarted = expectation(description: "restarted with committed settings")
        delegate.onStart = {
            XCTAssertEqual(delegate.settings.virtualDisplayCount, 1)
            XCTAssertEqual(delegate.settings.streamedDisplayIndex, 0)
            restarted.fulfill()
        }

        delegate.settings.virtualDisplayCount = 1
        await fulfillment(of: [restarted], timeout: 2)

        XCTAssertEqual(delegate.startCount, 1)
        XCTAssertEqual(delegate.stopCount, 1)
        delegate.onStart = nil
    }

    @MainActor
    func testConcurrentStartIsIgnoredAndStopInvalidatesSuspendedStart() async {
        let delegate = SuspendedStartAppDelegate()
        delegate.settings = DisplaySettings(defaults: defaults)
        delegate.settings.hasScreenRecordingPermission = true
        delegate.settings.displaySource = .currentMain
        delegate.settings.connectionMode = .wireless
        let prepared = expectation(description: "startup reached display wait")
        delegate.onPrepare = { prepared.fulfill() }
        var creationCount = 0
        let subscription = delegate.settings.$displayCreated.dropFirst().filter { $0 }.sink { _ in
            creationCount += 1
        }
        defer { subscription.cancel() }

        let firstStart = Task { @MainActor in await delegate.startServer() }
        await fulfillment(of: [prepared], timeout: 2)
        await delegate.startServer()
        delegate.stopServer()
        delegate.resumePreparation()
        await firstStart.value

        XCTAssertEqual(creationCount, 1)
        XCTAssertFalse(delegate.settings.isRunning)
        XCTAssertFalse(delegate.settings.displayCreated)
        XCTAssertNil(delegate.screenCapture)
        XCTAssertNil(delegate.streamingServer)
    }

    @MainActor
    func testStoppedStartupCannotTearDownReplacementStartup() async {
        let delegate = SuspendedStartAppDelegate()
        delegate.settings = DisplaySettings(defaults: defaults)
        delegate.settings.hasScreenRecordingPermission = true
        delegate.settings.displaySource = .currentMain
        delegate.settings.connectionMode = .wireless
        let firstPrepared = expectation(description: "first startup suspended")
        delegate.onPrepare = { firstPrepared.fulfill() }
        let firstStart = Task { @MainActor in await delegate.startServer() }
        await fulfillment(of: [firstPrepared], timeout: 2)
        delegate.stopServer()

        let replacementPrepared = expectation(description: "replacement startup suspended")
        delegate.onPrepare = { replacementPrepared.fulfill() }
        let replacementStart = Task { @MainActor in await delegate.startServer() }
        await fulfillment(of: [replacementPrepared], timeout: 2)

        delegate.resumePreparation()
        await firstStart.value
        XCTAssertTrue(delegate.settings.displayCreated)

        delegate.stopServer()
        delegate.resumePreparation()
        await replacementStart.value
        XCTAssertFalse(delegate.settings.displayCreated)
        XCTAssertNil(delegate.streamingServer)
    }

    @MainActor
    func testLinuxTriggerRequiresExplicitUsernameBeforeStartingServer() async {
        let delegate = RecordingAppDelegate()
        delegate.settings = DisplaySettings(defaults: defaults)
        delegate.settings.linuxTargetIP = "100.64.0.2"
        delegate.settings.linuxSSHUser = "  \n"

        await delegate.triggerLinuxClient()

        XCTAssertEqual(delegate.startCount, 0)
        XCTAssertEqual(delegate.settings.connectionMode, .usb)
        XCTAssertTrue(delegate.settings.linuxTriggerStatus.contains("SSH username"))
    }
}

private final class SuspendedStartAppDelegate: AppDelegate {
    var onPrepare: (() -> Void)?
    private var preparations: [CheckedContinuation<Void, Never>] = []

    @MainActor
    override func prepareDisplayForCapture(connectionMode: ConnectionMode) async {
        await withCheckedContinuation { continuation in
            preparations.append(continuation)
            onPrepare?()
        }
    }

    func resumePreparation() {
        guard !preparations.isEmpty else { return }
        preparations.removeFirst().resume()
    }
}

private final class RecordingAppDelegate: AppDelegate {
    var startCount = 0
    var stopCount = 0
    var onStart: (() -> Void)?

    @MainActor
    override func startServer() async {
        startCount += 1
        settings.isRunning = true
        onStart?()
    }

    override func stopServer() {
        stopCount += 1
        settings.isRunning = false
    }
}
