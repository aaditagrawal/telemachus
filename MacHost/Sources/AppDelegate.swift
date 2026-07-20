import Cocoa
import SwiftUI
import Combine
import ApplicationServices
import os.log
@preconcurrency import ScreenCaptureKit

private enum TelemachusLog {
    static let unified = Logger(
        subsystem: "dev.telemachus.display",
        category: "runtime"
    )
    static let lock = NSLock()
    static let maximumFileSize: UInt64 = 1_048_576

    static func write(_ message: String) {
        // Dynamic details such as device names and addresses remain private in
        // the unified log. The local file is mode 0600 and rotates at 1 MiB.
        unified.debug("\(message, privacy: .private)")
        lock.withLock {
            do {
                let directory = FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent("Library/Logs/Telemachus", isDirectory: true)
                try FileManager.default.createDirectory(
                    at: directory,
                    withIntermediateDirectories: true,
                    attributes: [.posixPermissions: 0o700]
                )
                let url = directory.appendingPathComponent("telemachus.log")
                let rotatedURL = directory.appendingPathComponent("telemachus.log.1")
                let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                if size >= maximumFileSize {
                    try? FileManager.default.removeItem(at: rotatedURL)
                    try? FileManager.default.moveItem(at: url, to: rotatedURL)
                }
                let timestamp = ISO8601DateFormatter().string(from: Date())
                let data = Data("\(timestamp) \(message)\n".utf8)
                if !FileManager.default.fileExists(atPath: url.path) {
                    FileManager.default.createFile(
                        atPath: url.path,
                        contents: data,
                        attributes: [.posixPermissions: 0o600]
                    )
                } else {
                    let handle = try FileHandle(forWritingTo: url)
                    try handle.seekToEnd()
                    try handle.write(contentsOf: data)
                    try handle.close()
                }
            } catch {
                unified.error("File logging failed: \(error.localizedDescription, privacy: .private)")
            }
        }
    }
}

func debugLog(_ message: String) {
    TelemachusLog.write(message)
}

// MARK: - Gesture State Machine

enum GestureState {
    case idle
    case pending          // Touch down, waiting to determine gesture
    case scrolling        // 1-finger scroll
    case longPressReady   // Long press detected, waiting for drag or release
    case dragging         // Long press + drag (left mouse drag)
    case twoFingerScroll  // 2-finger scroll
    case pinching         // Pinch zoom
}

struct GestureThresholds {
    static let tapMaxDistance: CGFloat = 15
    static let tapMaxTime: UInt64 = 250_000_000       // 250ms
    static let doubleTapMaxTime: UInt64 = 400_000_000  // 400ms
    static let doubleTapMaxDistance: CGFloat = 20
    static let longPressTime: UInt64 = 500_000_000     // 500ms
    static let scrollSensitivity: CGFloat = 1.2
    static let pinchMinDistance: CGFloat = 20
    static let minTouchInterval: UInt64 = 8_000_000    // ~120Hz
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var streamingServer: StreamingServer?
    var screenCapture: ScreenCapture?
    var virtualDisplayManager: VirtualDisplayManager?
    /// The display currently being captured. This may be a Telemachus-created
    /// extension or an existing macOS display such as Screen Sharing Virtual Display.
    private var activeDisplayID: CGDirectDisplayID?
    var settings = DisplaySettings()
    var settingsWindow: SettingsWindowController?
    var statusItem: NSStatusItem?
    let pairedDeviceStore = PairedDeviceStore()
    /// Name of the wireless device currently streaming (nil when no wireless client is active).
    /// Used to roll its `lastConnected` timestamp forward every status refresh tick so the UI
    /// shows "just now" while connected and freezes at the disconnect moment afterward.
    private var currentWirelessDevice: String?
    private var cancellables = Set<AnyCancellable>()
    private var permissionCheckTimer: Timer?
    private var statusRefreshTimer: Timer?
    private var permissionMonitoringReady = false
    var isDaemonMode = false // Deprecated: keeping variable for ABI compatibility but unused

    func applicationDidFinishLaunching(_ notification: Notification) {
        print("✅ App launched")

        // Seed permission state synchronously so the first visible window never
        // flashes the onboarding flow for an already-authorized installation.
        settings.hasScreenRecordingPermission = CGPreflightScreenCaptureAccess()
        settings.hasAccessibilityPermission = AXIsProcessTrusted()

        // Create menu bar item
        setupMenuBar()

        // Setup settings window
        setupSettingsWindow()

        // Setup settings observers
        setupSettingsObservers()

        // Check permissions
        Task {
            await checkPermissions()
            await MainActor.run {
                permissionMonitoringReady = true
            }
        }

        // Notice grants made while System Settings is open. This keeps first-run
        // setup plug-and-play: once Screen Recording is enabled, an auto-start
        // configuration can begin streaming without another click in Telemachus.
        permissionCheckTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.refreshPermissionState()
            }
        }

        // Periodic status refresh for the per-mode checklist (ADB / WiFi / Listening IP).
        statusRefreshTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.refreshStatusIndicators()
            }
        }
        // Initial refresh so the UI isn't blank for 2 seconds.
        Task { @MainActor in
            refreshStatusIndicators()
        }

        if CommandLine.arguments.contains("--headless-benchmark") {
            debugLog("Headless benchmark mode: settings window suppressed")
        } else if #available(macOS 13.0, *) {
            if DaemonManager.shared.isEnabled && settings.hasCompletedOnboarding {
                print("🚀 Launch at Login is enabled - starting silently in background")
                // Do not show settings window automatically.
                // applicationShouldHandleReopen will show it if the user manually launched the app.
            } else {
                showSettings()
            }
        } else {
            showSettings()
        }

        // Declarative auto-start (no Mac interaction): start the server in the
        // chosen Startup mode if enabled. No blocking permission modal here —
        // it cannot be acted on when the Mac is headless.
        if settings.autoStartStreamingOnLaunch {
            settings.connectionMode = settings.startupMode
            Task {
                await self.checkPermissions()
                if self.settings.hasScreenRecordingPermission {
                    await self.startServer()
                } else {
                    debugLog("Auto-start skipped: Screen Recording permission not granted")
                }
            }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            showSettings()
        }
        return true
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        Task { @MainActor in
            refreshPermissionState()
        }
    }

    @MainActor
    private func refreshPermissionState() {
        guard permissionMonitoringReady else { return }

        let hadScreenRecording = settings.hasScreenRecordingPermission
        let hasScreenRecording = CGPreflightScreenCaptureAccess()
        settings.hasScreenRecordingPermission = hasScreenRecording
        settings.hasAccessibilityPermission = AXIsProcessTrusted()

        guard hasScreenRecording, !hadScreenRecording else { return }
        debugLog("Screen Recording permission became available while app was running")

        if settings.autoStartStreamingOnLaunch && !settings.isRunning {
            Task {
                await startServer()
            }
        }
    }

    @MainActor
    private func refreshStatusIndicators() {
        settings.adbInstalled = StatusDetector.adbInstalled()
        settings.wifiConnected = StatusDetector.wifiReachable()
        settings.listeningAddress = LANAddressResolver.primaryIPv4()

        // While a wireless client is actively streaming, keep its lastConnected
        // rolling forward so the UI shows "just now". On disconnect, the
        // onClientDisconnected handler clears currentWirelessDevice — from that
        // point lastConnected stays frozen at the disconnect moment, so the
        // "X minutes ago" label counts up correctly.
        if let name = currentWirelessDevice {
            pairedDeviceStore.upsert(name: name, lastConnected: Date())
        }

        let port = Int(settings.port)
        let selectedSerial = settings.adbDeviceSerial
        Task.detached { [weak self] in
            let devices = StatusDetector.usbDevices()
            let effectiveSerial: String?
            if devices.contains(selectedSerial) {
                effectiveSerial = selectedSerial
            } else {
                effectiveSerial = devices.first
            }
            let reverseOK = StatusDetector.adbReverseConfigured(
                port: port,
                serial: effectiveSerial
            )
            await MainActor.run { [weak self] in
                guard let self = self else { return }
                
                let isConnected = !devices.isEmpty

                self.settings.usbDeviceConnected = isConnected
                self.settings.availableADBDevices = devices
                if let effectiveSerial,
                   self.settings.adbDeviceSerial != effectiveSerial {
                    self.settings.adbDeviceSerial = effectiveSerial
                }
                self.settings.adbReverseConfigured = reverseOK

                // Self-healing USB bridge (level-triggered, not edge-triggered):
                // whenever we are in USB mode with the server running and a
                // device present but adb reverse missing, (re)establish it.
                // Covers replug, adb-server restart, etc. The server lifecycle
                // is NOT tied to device events — it stays up and the tablet
                // reconnects via its own connect button.
                if self.settings.connectionMode == .usb
                    && isConnected
                    && self.settings.isRunning
                    && !reverseOK {
                    debugLog("🔌 USB bridge missing while running — (re)establishing adb reverse")
                    Task { await self.setupADBReverse() }
                }
            }
        }
    }

    @MainActor
    private func handleConnectionModeChange(to mode: ConnectionMode) async {
        debugLog("Connection mode changed to: \(mode.rawValue)")
        // Disconnect any active client immediately (per spec §6 / fix #2).
        let wasRunning = settings.isRunning
        if wasRunning {
            stopServer()
        }
        if mode == .wireless {
            // Generate token if missing; the QR will reflect it.
            do {
                _ = try WirelessAuth.loadOrCreate()
                settings.wirelessTokenError = nil
            } catch {
                settings.wirelessTokenError = error.localizedDescription
                debugLog("Could not prepare wireless token: \(error.localizedDescription)")
            }
        }
        if wasRunning {
            await startServer()
        }
    }

    /// Check permissions on demand (called when settings window opens or manually)
    func refreshPermissions() {
        Task {
            await checkPermissions()
        }
    }

    func setupSettingsObservers() {
        // Observer cho gaming boost changes
        settings.$gamingBoost
            .dropFirst() // Skip initial value
            .sink { [weak self] gamingBoost in
                guard let self = self, self.settings.isRunning else { return }
                print("🎮 Gaming Boost \(gamingBoost ? "ENABLED" : "DISABLED")")
                self.screenCapture?.updateEncoderSettings(
                    bitrateMbps: self.settings.effectiveBitrate,
                    quality: self.settings.effectiveQuality,
                    gamingBoost: gamingBoost
                )
            }
            .store(in: &cancellables)

        // Observer cho bitrate/quality changes (chỉ khi không gaming boost)
        Publishers.CombineLatest(settings.$bitrate, settings.$quality)
            .dropFirst()
            .sink { [weak self] bitrate, quality in
                guard let self = self, self.settings.isRunning, !self.settings.gamingBoost else { return }
                print("⚙️ Settings updated: \(bitrate)Mbps, \(quality)")
                self.screenCapture?.updateEncoderSettings(
                    bitrateMbps: bitrate,
                    quality: quality,
                    gamingBoost: false
                )
            }
            .store(in: &cancellables)

        // Observer cho rotation changes - send to connected client immediately
        settings.$rotation
            .dropFirst()
            .sink { [weak self] rotation in
                guard let self = self, self.settings.isRunning else { return }
                print("🔄 Rotation changed to \(rotation)°")
                self.streamingServer?.updateRotation(rotation)
            }
            .store(in: &cancellables)

        // Observer cho touch enable/disable - propagate to streaming server so
        // incoming touch frames from the client are dropped early when off.
        settings.$touchEnabled
            .dropFirst()
            .sink { [weak self] enabled in
                self?.streamingServer?.touchEnabled = enabled
            }
            .store(in: &cancellables)

        // Observer cho connection mode changes — restart server with new auth/ADB policy.
        settings.$connectionMode
            .dropFirst()
            .sink { [weak self] mode in
                guard let self = self else { return }
                Task { @MainActor in
                    await self.handleConnectionModeChange(to: mode)
                }
            }
            .store(in: &cancellables)

        // Observer cho resolution changes — the virtual display is created at
        // server start, so a new resolution (list row or custom Apply) needs a
        // stop/start cycle to take effect, same as a connection-mode change.
        // Without this, changing resolution mid-run silently did nothing.
        settings.$resolution
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] resolution in
                guard let self = self else { return }
                Task { @MainActor in
                    guard self.settings.isRunning else { return }
                    debugLog("Resolution changed to \(resolution) — restarting server to rebuild virtual display")
                    self.stopServer()
                    await self.startServer()
                }
            }
            .store(in: &cancellables)

        settings.$displaySource
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] source in
                guard let self else { return }
                Task { @MainActor in
                    guard self.settings.isRunning else { return }
                    debugLog("Display source changed to \(source.rawValue) — restarting capture")
                    self.stopServer()
                    await self.startServer()
                }
            }
            .store(in: &cancellables)
    }

    func setupMenuBar() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "display.2", accessibilityDescription: "Virtual Display")
        }

        let menu = NSMenu()

        menu.addItem(NSMenuItem(title: "Settings", action: #selector(showSettings), keyEquivalent: "s"))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))

        statusItem?.menu = menu
    }

    func setupSettingsWindow() {
        settingsWindow = SettingsWindowController(settings: settings)

        settings.onToggleServer = { [weak self] in
            guard let self else { return }
            if self.settings.isRunning {
                self.stopServer()
            } else {
                Task { [weak self] in
                    await self?.startServer()
                }
            }
        }

        settings.onRequestScreenRecordingPermission = { [weak self] in
            guard let appDelegate = self else { return }
            Task { @MainActor in
                appDelegate.requestScreenRecordingPermission()
            }
        }

        settings.onRequestAccessibilityPermission = { [weak self] in
            guard let appDelegate = self else { return }
            Task { @MainActor in
                appDelegate.requestAccessibilityPermission()
            }
        }

        settings.onResetWirelessToken = { [weak self] in
            do {
                let token = try WirelessAuth.reset()
                self?.settings.wirelessTokenError = nil
                self?.streamingServer?.rotateAuthToken(token)
                return true
            } catch {
                self?.settings.wirelessTokenError = error.localizedDescription
                debugLog("Could not reset wireless token: \(error.localizedDescription)")
                return false
            }
        }
    }

    @objc func showSettings() {
        settingsWindow?.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func checkPermissions() async {
        let version = ProcessInfo.processInfo.operatingSystemVersion
        debugLog("checkPermissions — macOS \(version.majorVersion).\(version.minorVersion).\(version.patchVersion)")

        // Check Screen Recording permission using CoreGraphics API
        let hasScreenCapture = CGPreflightScreenCaptureAccess()
        await MainActor.run {
            settings.hasScreenRecordingPermission = hasScreenCapture
        }
        if hasScreenCapture {
            debugLog("Screen recording permission granted (CGPreflight)")

            // On macOS 26+, also verify ScreenCaptureKit is actually functional
            if version.majorVersion >= 26 {
                do {
                    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                    debugLog("SCShareableContent verification OK — \(content.displays.count) displays found")
                } catch {
                    debugLog("WARNING: CGPreflight OK but SCShareableContent failed on macOS 26: \(error.localizedDescription)")
                    debugLog("CGDisplayStream fallback will likely activate at capture time")
                }
            }
        } else {
            debugLog("Screen recording permission not granted yet")
        }

        // Check Accessibility permission (required for touch/mouse injection)
        await checkAccessibilityPermission()
    }

    func checkAccessibilityPermission() async {
        let trusted = AXIsProcessTrusted()
        await MainActor.run {
            settings.hasAccessibilityPermission = trusted
        }
        if trusted {
            print("✅ Accessibility permission granted")
        } else {
            print("⚠️  Accessibility permission not granted - touch control will not work")
        }
    }

    @MainActor
    func requestScreenRecordingPermission() {
        let granted = CGRequestScreenCaptureAccess()
        settings.hasScreenRecordingPermission = granted || CGPreflightScreenCaptureAccess()

        if !settings.hasScreenRecordingPermission,
           let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") {
            NSWorkspace.shared.open(url)
        }
    }

    @MainActor
    func requestAccessibilityPermission() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        let trusted = AXIsProcessTrustedWithOptions(options)
        settings.hasAccessibilityPermission = trusted

        if !trusted {
            print("⚠️  User needs to grant Accessibility permission in System Settings")
            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
                NSWorkspace.shared.open(url)
            }
        }
    }

    /// Setup ADB reverse port forwarding for USB connection
    func setupADBReverse() async {
        let port = settings.port
        let configuredSerial = settings.adbDeviceSerial
        print("🔌 Setting up ADB reverse for port \(port)...")
        debugLog("🔌 setupADBReverse() invoked for port \(port)...")

        await Task.detached(priority: .utility) {
            // Try common adb paths
            let adbPaths = [
                "/usr/local/bin/adb",
                "/opt/homebrew/bin/adb",
                "~/Library/Android/sdk/platform-tools/adb",
                "/Users/\(NSUserName())/Library/Android/sdk/platform-tools/adb"
            ]

            var adbPath: String?
            for path in adbPaths {
                let expandedPath = NSString(string: path).expandingTildeInPath
                if FileManager.default.fileExists(atPath: expandedPath) {
                    adbPath = expandedPath
                    break
                }
            }

            // Also try 'which adb' to find it in PATH
            if adbPath == nil {
                let whichProcess = Process()
                whichProcess.executableURL = URL(fileURLWithPath: "/usr/bin/which")
                whichProcess.arguments = ["adb"]
                let whichPipe = Pipe()
                whichProcess.standardOutput = whichPipe
                whichProcess.standardError = FileHandle.nullDevice

                do {
                    try whichProcess.run()
                    whichProcess.waitUntilExit()
                    let data = whichPipe.fileHandleForReading.readDataToEndOfFile()
                    if let path = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
                       !path.isEmpty {
                        adbPath = path
                    }
                } catch {
                    // Ignore
                }
            }

            guard let finalAdbPath = adbPath else {
                print("⚠️  ADB not found - USB connection may not work")
                print("💡 Install Android SDK or run manually: adb reverse tcp:\(port) tcp:\(port)")
                return
            }

            print("📱 Found ADB at: \(finalAdbPath)")
            let connectedSerials = StatusDetector.usbDevices()
            let targetSerial = connectedSerials.contains(configuredSerial)
                ? configuredSerial
                : connectedSerials.first

            // Retry adb reverse up to 3 times — handles first-install authorization delay
            for attempt in 1...3 {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: finalAdbPath)
                process.arguments = StatusDetector.adbArguments(
                    serial: targetSerial,
                    command: ["reverse", "tcp:\(port)", "tcp:\(port)"]
                )

                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = pipe

                do {
                    try process.run()
                    process.waitUntilExit()

                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    let output = String(data: data, encoding: .utf8) ?? ""

                    if process.terminationStatus == 0 {
                        print("✅ ADB reverse setup successful: tcp:\(port) -> tcp:\(port)")
                        Self.launchAndroidClient(
                            adbPath: finalAdbPath,
                            serial: targetSerial
                        )
                        return
                    } else {
                        print("⚠️  ADB reverse attempt \(attempt)/3 failed: \(output.trimmingCharacters(in: .whitespacesAndNewlines))")
                        if attempt < 3 {
                            try? await Task.sleep(nanoseconds: 1_000_000_000)
                        }
                    }
                } catch {
                    print("⚠️  Failed to run ADB (attempt \(attempt)/3): \(error.localizedDescription)")
                    if attempt < 3 {
                        try? await Task.sleep(nanoseconds: 1_000_000_000)
                    }
                }
            }

            print("💡 Make sure Android device is connected via USB with debugging enabled")
        }.value
    }

    /// Bring the Android receiver to the foreground and ask it to connect.
    /// `am start` is idempotent because MainActivity uses singleTop and handles
    /// repeated intents, which gives us cable-driven plug-and-play after setup.
    private static func launchAndroidClient(adbPath: String, serial: String?) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: adbPath)
        process.arguments = StatusDetector.adbArguments(serial: serial, command: [
            "shell", "am", "start",
            "-a", "android.intent.action.MAIN",
            "-n", "dev.telemachus.display/.MainActivity",
            "--ez", "auto_connect", "true"
        ])
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe

        do {
            try process.run()
            process.waitUntilExit()
            let output = String(
                data: pipe.fileHandleForReading.readDataToEndOfFile(),
                encoding: .utf8
            )?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if process.terminationStatus == 0 {
                debugLog("📱 Android client launched for automatic USB connection")
            } else {
                debugLog("Android auto-launch unavailable: \(output)")
            }
        } catch {
            debugLog("Android auto-launch failed: \(error.localizedDescription)")
        }
    }

    @MainActor
    func showPermissionAlert() {
        let version = ProcessInfo.processInfo.operatingSystemVersion
        let isMacOS26 = version.majorVersion >= 26

        let alert = NSAlert()
        if isMacOS26 {
            alert.messageText = "Screen & System Audio Recording Permission Required"
            alert.informativeText = "Please grant Screen & System Audio Recording permission in System Settings > Privacy & Security."
        } else {
            alert.messageText = "Screen Recording Permission Required"
            alert.informativeText = "Please grant Screen Recording permission in System Settings > Privacy & Security."
        }
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Open System Settings")
        alert.addButton(withTitle: "Later")

        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!)
        }
    }

    func startServer() async {
        debugLog("🚀 startServer() invoked. Check permission: \(settings.hasScreenRecordingPermission)")
        guard settings.hasScreenRecordingPermission else {
            debugLog("❌ startServer aborted: Missing Screen Recording permission")
            await showPermissionAlert()
            return
        }

        do {
            let size = settings.resolutionSize
            let captureDisplayID: CGDirectDisplayID
            let streamSize: (width: Int, height: Int)

            switch settings.displaySource {
            case .extended:
                let manager = VirtualDisplayManager()
                virtualDisplayManager = manager
                try manager.createDisplay(
                    width: size.width,
                    height: size.height,
                    refreshRate: settings.refreshRate,
                    hiDPI: settings.hiDPI,
                    name: "Telemachus"
                )

                // Disable mirror mode (may fail if already in extend mode).
                try? manager.disableMirrorMode()
                guard let createdID = manager.displayID else {
                    throw VirtualDisplayError.creationFailed("Display was created without a display ID")
                }
                captureDisplayID = createdID
                streamSize = size

            case .currentMain:
                virtualDisplayManager = nil
                captureDisplayID = CGMainDisplayID()
                streamSize = Self.aspectFitStreamSize(
                    sourceWidth: CGDisplayPixelsWide(captureDisplayID),
                    sourceHeight: CGDisplayPixelsHigh(captureDisplayID),
                    maximumWidth: size.width,
                    maximumHeight: size.height
                )
                debugLog(
                    "Using existing main display \(captureDisplayID): " +
                    "\(CGDisplayPixelsWide(captureDisplayID))x\(CGDisplayPixelsHigh(captureDisplayID)), " +
                    "streaming \(streamSize.width)x\(streamSize.height)"
                )
            }
            activeDisplayID = captureDisplayID

            await MainActor.run {
                settings.displayCreated = true
            }

            // Run ADB setup (USB only) and display init wait in parallel.
            // For wireless mode, skip ADB entirely — the auth handshake gates LAN connections instead.
            await withTaskGroup(of: Void.self) { group in
                if settings.connectionMode == .usb {
                    group.addTask { await self.setupADBReverse() }
                } else {
                    debugLog("Wireless mode: skipping ADB setup")
                }
                group.addTask { try? await Task.sleep(nanoseconds: 500_000_000) }
            }

            if let vdm = virtualDisplayManager {
                vdm.restoreDisplayPosition()
                let registered = vdm.verifyDisplayRegistered()
                if !registered {
                    debugLog("WARNING: Virtual display not found in online display list — capture may fail")
                }
            }

            // Setup capture
            screenCapture = try await ScreenCapture()
            screenCapture?.onCaptureMethodChanged = { [weak self] method in
                guard let self = self else { return }
                debugLog("Capture method: \(method)")
                Task { @MainActor in
                    self.settings.captureMethod = method
                }
            }
            screenCapture?.onDisplayIDChanged = { [weak self] displayID in
                guard let appDelegate = self else { return }
                Task { @MainActor in
                    appDelegate.activeDisplayID = displayID
                }
            }
            let existingDisplayOutput = settings.displaySource == .currentMain ? streamSize : nil
            try await screenCapture?.setupForDisplay(
                captureDisplayID,
                refreshRate: settings.effectiveRefreshRate,
                outputSize: existingDisplayOutput,
                followsMainDisplay: settings.displaySource == .currentMain
            )

            // Setup server. USB is loopback-only; wireless authenticates every
            // candidate before it can replace the active client.
            let serverMode: StreamingServerMode
            if settings.connectionMode == .wireless {
                serverMode = .wireless(
                    authToken: try WirelessAuth.loadOrCreate()
                )
                settings.wirelessTokenError = nil
            } else {
                serverMode = .usb
            }
            streamingServer = StreamingServer(
                port: settings.port,
                mode: serverMode
            )
            streamingServer?.touchEnabled = settings.touchEnabled
            if settings.connectionMode == .wireless {
                streamingServer?.onWirelessClientPaired = { [weak self] deviceName in
                    guard let self = self else { return }
                    Task { @MainActor in
                        self.currentWirelessDevice = deviceName
                        self.settings.currentWirelessDevice = deviceName
                        self.pairedDeviceStore.upsert(name: deviceName, lastConnected: Date())
                    }
                }
            }
            // Send the LOGICAL resolution that the user picked. The H.264 SPS in
            // the stream still carries the true physical pixel dimensions, so the
            // Android decoder/MediaCodec sets up correctly regardless. Sending the
            // logical dimensions here makes the resolution overlay on Android
            // match the Mac's resolution dropdown (e.g. "2560x1600" instead of
            // the HiDPI-doubled "5120x3200").
            streamingServer?.setDisplaySize(width: streamSize.width, height: streamSize.height, rotation: settings.rotation)
            streamingServer?.onClientConnected = { [weak self] in
                guard let self = self else { return }
                self.screenCapture?.requestKeyframeOrReplayCachedFrame(force: true)
                Task { @MainActor in
                    // Clear before the new client's type-11 arrives so a
                    // takeover never leaves the previous tablet's model/Hz up.
                    self.settings.connectedDeviceModel = nil
                    self.settings.connectedDeviceMaxRefreshRate = nil
                    self.settings.clientConnected = true
                }
            }
            // Runs synchronously on the server's network queue BEFORE the
            // display config is sent, so the config below carries the right
            // dimensions for the negotiated codec.
            streamingServer?.onCodecNegotiated = { [weak self] codec in
                guard let self = self, let capture = self.screenCapture else { return }
                capture.setCodec(codec)
                switch codec {
                case .hevc:
                    self.streamingServer?.setDisplaySize(
                        width: streamSize.width,
                        height: streamSize.height,
                        rotation: self.settings.rotation
                    )
                case .h264:
                    // Clamped physical encode size: the client must configure
                    // its (weak) AVC decoder within its supported range, and
                    // this matches what the stream's SPS will carry.
                    let enc = capture.encodeSize(for: .h264)
                    self.streamingServer?.setDisplaySize(width: enc.width, height: enc.height, rotation: self.settings.rotation)
                }
            }
            streamingServer?.onKeyframeRequested = { [weak self] force in
                self?.screenCapture?.requestKeyframeOrReplayCachedFrame(force: force)
            }

            streamingServer?.onClientDisconnected = { [weak self] in
                guard let self = self else { return }
                Task { @MainActor in
                    self.settings.clientConnected = false
                    self.settings.connectedDeviceModel = nil
                    self.settings.connectedDeviceMaxRefreshRate = nil
                    // Final lastConnected snapshot at the disconnect moment, then
                    // freeze (currentWirelessDevice = nil stops the rolling update
                    // in refreshStatusIndicators).
                    if let name = self.currentWirelessDevice {
                        self.pairedDeviceStore.upsert(name: name, lastConnected: Date())
                        self.currentWirelessDevice = nil
                        self.settings.currentWirelessDevice = nil
                    }
                }
            }

            streamingServer?.onDeviceInfoReceived = { [weak self] model, refreshRate in
                guard let self = self else { return }
                Task { @MainActor in
                    self.settings.connectedDeviceModel = model
                    self.settings.connectedDeviceMaxRefreshRate = Int(refreshRate)
                    debugLog("Device info: \(model), \(refreshRate)Hz")
                }
            }

            streamingServer?.onTouchEvent = { [weak self] x, y, action, pointerCount, x2, y2 in
                self?.handleTouch(x: x, y: y, action: action, pointerCount: pointerCount, x2: x2, y2: y2)
            }

            streamingServer?.onStats = { [weak self] fps, mbps in
                let captured = self
                Task { @MainActor in
                    captured?.settings.currentFPS = fps
                    captured?.settings.currentBitrate = mbps
                }
            }

            streamingServer?.onServerFailed = { [weak self] error in
                debugLog("Streaming listener stopped: \(error.localizedDescription)")
                self?.performSelector(
                    onMainThread: #selector(AppDelegate.handleServerFailure),
                    with: nil,
                    waitUntilDone: false
                )
            }

            try streamingServer?.start()
            try await screenCapture?.startStreaming(
                to: streamingServer,
                bitrateMbps: settings.effectiveBitrate,
                quality: settings.effectiveQuality,
                gamingBoost: settings.gamingBoost,
                frameRate: settings.effectiveRefreshRate
            )

            await MainActor.run {
                settings.isRunning = true
            }

            print("✅ Server started on port \(settings.port)")
        } catch {
            print("❌ Failed to start: \(error)")
            await MainActor.run {
                teardownStreamingComponents()
                settings.isRunning = false
                settings.displayCreated = false

                let alert = NSAlert()
                alert.messageText = "Failed to Start Server"
                alert.informativeText = error.localizedDescription
                alert.alertStyle = .critical
                alert.runModal()
            }
        }
    }

    /// Idempotent cleanup used for both normal shutdown and a failure after any
    /// partial combination of display, capture, listener, or ADB setup.
    private func teardownStreamingComponents() {
        screenCapture?.stopStreaming()
        streamingServer?.stop()
        virtualDisplayManager?.destroyDisplay()
        screenCapture = nil
        streamingServer = nil
        virtualDisplayManager = nil
        activeDisplayID = nil
    }

    @objc private func handleServerFailure() {
        guard settings.isRunning else { return }
        stopServer()
    }

    /// Scale an existing display into the requested stream bounds without
    /// stretching it. VideoToolbox requires even dimensions for reliable
    /// hardware H.264/HEVC operation, so both axes are rounded down to even.
    static func aspectFitStreamSize(
        sourceWidth: Int,
        sourceHeight: Int,
        maximumWidth: Int,
        maximumHeight: Int
    ) -> (width: Int, height: Int) {
        guard sourceWidth > 0, sourceHeight > 0, maximumWidth > 0, maximumHeight > 0 else {
            return (max(2, maximumWidth & ~1), max(2, maximumHeight & ~1))
        }

        let scale = min(
            1.0,
            Double(maximumWidth) / Double(sourceWidth),
            Double(maximumHeight) / Double(sourceHeight)
        )
        let fittedWidth = max(2, Int(floor(Double(sourceWidth) * scale)) & ~1)
        let fittedHeight = max(2, Int(floor(Double(sourceHeight) * scale)) & ~1)
        return (fittedWidth, fittedHeight)
    }

    func stopServer() {
        // Save display position before destroying
        virtualDisplayManager?.saveDisplayPosition()

        teardownStreamingComponents()

        settings.isRunning = false
        settings.displayCreated = false
        settings.clientConnected = false
        settings.connectedDeviceModel = nil
        settings.connectedDeviceMaxRefreshRate = nil
        settings.currentFPS = 0
        settings.currentBitrate = 0

        print("⏹️ Server stopped")
    }

    // MARK: - Gesture Properties

    private let eventSource = CGEventSource(stateID: .hidSystemState)
    private var accessibilityWarningShown = false
    private var gestureState: GestureState = .idle
    private var lastTouchTime: UInt64 = 0

    // Touch tracking
    private var touchStartPosition: CGPoint = .zero
    private var touchLastPosition: CGPoint = .zero
    private var touchStartTime: UInt64 = 0
    private var touchLastMoveTime: UInt64 = 0
    private var lastScrollDeltaX: CGFloat = 0
    private var lastScrollDeltaY: CGFloat = 0

    // Double tap tracking
    private var lastTapTime: UInt64 = 0
    private var lastTapPosition: CGPoint = .zero

    // Long press timer
    private var longPressTimer: DispatchWorkItem?

    // 2-finger tracking
    private var initialPinchDistance: CGFloat = 0
    private var lastPinchDistance: CGFloat = 0

    // Momentum scrolling
    private var momentumTimer: Timer?
    private var momentumVelocityX: CGFloat = 0
    private var momentumVelocityY: CGFloat = 0
    private var lastMomentumPosition: CGPoint = .zero

    // MARK: - Touch Entry Point

    func handleTouch(x: Float, y: Float, action: Int, pointerCount: Int = 1, x2: Float = 0, y2: Float = 0) {
        guard settings.touchEnabled else { return }

        if !AXIsProcessTrusted() {
            if !accessibilityWarningShown {
                accessibilityWarningShown = true
                print("⚠️  Accessibility not granted - touch ignored")
                Task { @MainActor in
                    settings.hasAccessibilityPermission = false
                }
            }
            return
        }

        guard let displayID = activeDisplayID else { return }
        let bounds = CGDisplayBounds(displayID)

        let p1 = CGPoint(
            x: bounds.origin.x + CGFloat(x) * bounds.width,
            y: bounds.origin.y + CGFloat(y) * bounds.height
        )
        let p2 = CGPoint(
            x: bounds.origin.x + CGFloat(x2) * bounds.width,
            y: bounds.origin.y + CGFloat(y2) * bounds.height
        )

        if pointerCount >= 2 {
            handleTwoFingerTouch(p1: p1, p2: p2, action: action)
        } else {
            handleOneFingerTouch(at: p1, action: action)
        }
    }

    // MARK: - 1-Finger Gesture State Machine

    private func handleOneFingerTouch(at point: CGPoint, action: Int) {
        switch action {
        case 0: oneFingerDown(at: point)
        case 1: oneFingerMove(to: point)
        case 2: oneFingerUp(at: point)
        default: break
        }
    }

    private func oneFingerDown(at point: CGPoint) {
        stopMomentumScroll()
        cancelLongPressTimer()

        touchStartPosition = point
        touchLastPosition = point
        touchStartTime = DispatchTime.now().uptimeNanoseconds
        touchLastMoveTime = touchStartTime
        gestureState = .pending

        // Move cursor to touch position (absolute)
        moveCursor(to: point)

        // Start long press timer
        let timer = DispatchWorkItem { [weak self] in
            guard let self, self.gestureState == .pending else { return }
            self.gestureState = .longPressReady
        }
        longPressTimer = timer
        DispatchQueue.main.asyncAfter(
            deadline: .now() + .nanoseconds(Int(GestureThresholds.longPressTime)),
            execute: timer
        )
    }

    private func oneFingerMove(to point: CGPoint) {
        let now = DispatchTime.now().uptimeNanoseconds
        if now - lastTouchTime < GestureThresholds.minTouchInterval { return }
        lastTouchTime = now

        let deltaX = point.x - touchLastPosition.x
        let deltaY = point.y - touchLastPosition.y
        let totalDistance = hypot(point.x - touchStartPosition.x, point.y - touchStartPosition.y)

        switch gestureState {
        case .pending:
            if totalDistance > GestureThresholds.tapMaxDistance {
                cancelLongPressTimer()
                gestureState = .scrolling
                let sx = deltaX * GestureThresholds.scrollSensitivity
                let sy = deltaY * GestureThresholds.scrollSensitivity
                injectScrollEvent(deltaX: sx, deltaY: sy, at: point)
                lastScrollDeltaX = sx
                lastScrollDeltaY = sy
            }

        case .longPressReady:
            if totalDistance > GestureThresholds.tapMaxDistance {
                // Long press + drag → left mouse drag
                gestureState = .dragging
                injectMouseDown(at: touchStartPosition)
                injectMouseDragged(to: point)
            }

        case .scrolling:
            let sx = deltaX * GestureThresholds.scrollSensitivity
            let sy = deltaY * GestureThresholds.scrollSensitivity
            injectScrollEvent(deltaX: sx, deltaY: sy, at: point)
            let timeDelta = now - touchLastMoveTime
            if timeDelta > 0 && timeDelta < 100_000_000 {
                lastScrollDeltaX = sx
                lastScrollDeltaY = sy
            }

        case .dragging:
            injectMouseDragged(to: point)

        default:
            break
        }

        touchLastPosition = point
        touchLastMoveTime = now
    }

    private func oneFingerUp(at point: CGPoint) {
        cancelLongPressTimer()
        let now = DispatchTime.now().uptimeNanoseconds
        let elapsed = now - touchStartTime
        let distance = hypot(point.x - touchStartPosition.x, point.y - touchStartPosition.y)

        switch gestureState {
        case .pending:
            // Quick release, no movement → tap or double tap
            if distance < GestureThresholds.tapMaxDistance && elapsed < GestureThresholds.tapMaxTime {
                // Check double tap
                let timeSinceLastTap = now - lastTapTime
                let distFromLastTap = hypot(point.x - lastTapPosition.x, point.y - lastTapPosition.y)

                if timeSinceLastTap < GestureThresholds.doubleTapMaxTime
                    && distFromLastTap < GestureThresholds.doubleTapMaxDistance {
                    performDoubleClick(at: point)
                    lastTapTime = 0  // Reset so triple tap doesn't trigger
                } else {
                    performClick(at: point)
                    lastTapTime = now
                    lastTapPosition = point
                }
            }

        case .longPressReady:
            // Held long but didn't drag → right click
            performRightClick(at: point)

        case .scrolling:
            // Check momentum
            let timeSinceLastMove = now - touchLastMoveTime
            if timeSinceLastMove < 50_000_000 {
                let threshold: CGFloat = 2.0
                if abs(lastScrollDeltaX) > threshold || abs(lastScrollDeltaY) > threshold {
                    startMomentumScroll(
                        velocityX: lastScrollDeltaX * 6.0,
                        velocityY: lastScrollDeltaY * 6.0,
                        at: point
                    )
                }
            }

        case .dragging:
            injectMouseUp(at: point)

        default:
            break
        }

        gestureState = .idle
    }

    // MARK: - 2-Finger Gestures

    private func handleTwoFingerTouch(p1: CGPoint, p2: CGPoint, action: Int) {
        let distance = hypot(p2.x - p1.x, p2.y - p1.y)
        let midpoint = CGPoint(x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2)

        switch action {
        case 0: // Down
            cancelLongPressTimer()
            stopMomentumScroll()
            gestureState = .idle  // Reset so 2-finger detection starts fresh
            initialPinchDistance = distance
            lastPinchDistance = distance
            touchLastPosition = midpoint

        case 1: // Move
            let distanceChange = abs(distance - initialPinchDistance)
            let midDelta = hypot(midpoint.x - touchLastPosition.x, midpoint.y - touchLastPosition.y)

            // Determine mode if not yet decided
            if gestureState != .twoFingerScroll && gestureState != .pinching {
                if distanceChange > GestureThresholds.pinchMinDistance {
                    gestureState = .pinching
                } else if midDelta > GestureThresholds.tapMaxDistance {
                    gestureState = .twoFingerScroll
                }
            }

            switch gestureState {
            case .twoFingerScroll:
                let dx = (midpoint.x - touchLastPosition.x) * GestureThresholds.scrollSensitivity
                let dy = (midpoint.y - touchLastPosition.y) * GestureThresholds.scrollSensitivity
                injectScrollEvent(deltaX: dx, deltaY: dy, at: midpoint)

            case .pinching:
                let scaleDelta = distance - lastPinchDistance
                // Cmd + scroll = zoom in most Mac apps
                let zoomAmount = Int32(scaleDelta * 0.5)
                if zoomAmount != 0 {
                    injectZoomEvent(delta: zoomAmount, at: midpoint)
                }
                lastPinchDistance = distance

            default:
                break
            }

            touchLastPosition = midpoint

        case 2: // Up
            gestureState = .idle
            // Reset 1-finger tracking so leftover moves don't trigger scroll
            touchStartPosition = .zero
            touchLastPosition = .zero

        default:
            break
        }
    }

    // MARK: - Event Injection

    private func moveCursor(to point: CGPoint) {
        if let event = CGEvent(mouseEventSource: eventSource, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left) {
            event.post(tap: .cghidEventTap)
        }
    }

    private func performClick(at point: CGPoint) {
        if let down = CGEvent(mouseEventSource: eventSource, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left) {
            down.setIntegerValueField(.mouseEventClickState, value: 1)
            down.post(tap: .cghidEventTap)
        }
        if let up = CGEvent(mouseEventSource: eventSource, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) {
            up.setIntegerValueField(.mouseEventClickState, value: 1)
            up.post(tap: .cghidEventTap)
        }
    }

    private func performDoubleClick(at point: CGPoint) {
        if let down = CGEvent(mouseEventSource: eventSource, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left) {
            down.setIntegerValueField(.mouseEventClickState, value: 2)
            down.post(tap: .cghidEventTap)
        }
        if let up = CGEvent(mouseEventSource: eventSource, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) {
            up.setIntegerValueField(.mouseEventClickState, value: 2)
            up.post(tap: .cghidEventTap)
        }
    }

    private func performRightClick(at point: CGPoint) {
        if let down = CGEvent(mouseEventSource: eventSource, mouseType: .rightMouseDown, mouseCursorPosition: point, mouseButton: .right) {
            down.post(tap: .cghidEventTap)
        }
        if let up = CGEvent(mouseEventSource: eventSource, mouseType: .rightMouseUp, mouseCursorPosition: point, mouseButton: .right) {
            up.post(tap: .cghidEventTap)
        }
    }

    private func injectMouseDown(at point: CGPoint) {
        if let event = CGEvent(mouseEventSource: eventSource, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left) {
            event.setIntegerValueField(.mouseEventClickState, value: 1)
            event.post(tap: .cghidEventTap)
        }
    }

    private func injectMouseDragged(to point: CGPoint) {
        if let event = CGEvent(mouseEventSource: eventSource, mouseType: .leftMouseDragged, mouseCursorPosition: point, mouseButton: .left) {
            event.post(tap: .cghidEventTap)
        }
    }

    private func injectMouseUp(at point: CGPoint) {
        if let event = CGEvent(mouseEventSource: eventSource, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left) {
            event.post(tap: .cghidEventTap)
        }
    }

    private func injectScrollEvent(deltaX: CGFloat, deltaY: CGFloat, at position: CGPoint) {
        guard let scrollEvent = CGEvent(
            scrollWheelEvent2Source: eventSource,
            units: .pixel,
            wheelCount: 2,
            wheel1: Int32(deltaY),
            wheel2: Int32(deltaX),
            wheel3: 0
        ) else { return }
        scrollEvent.location = position
        scrollEvent.post(tap: .cghidEventTap)
    }

    private func injectZoomEvent(delta: Int32, at position: CGPoint) {
        guard let scrollEvent = CGEvent(
            scrollWheelEvent2Source: eventSource,
            units: .pixel,
            wheelCount: 1,
            wheel1: delta,
            wheel2: 0,
            wheel3: 0
        ) else { return }
        scrollEvent.location = position
        // Set Cmd flag for zoom
        scrollEvent.flags = .maskCommand
        scrollEvent.post(tap: .cghidEventTap)
    }

    // MARK: - Long Press Timer

    private func cancelLongPressTimer() {
        longPressTimer?.cancel()
        longPressTimer = nil
    }

    // MARK: - Momentum Scrolling

    private func startMomentumScroll(velocityX: CGFloat, velocityY: CGFloat, at position: CGPoint) {
        stopMomentumScroll()
        momentumVelocityX = velocityX
        momentumVelocityY = velocityY
        lastMomentumPosition = position
        momentumTimer = Timer.scheduledTimer(withTimeInterval: 0.016, repeats: true) { [weak self] _ in
            self?.momentumTick()
        }
    }

    private func momentumTick() {
        let decay: CGFloat = 0.92
        let minVelocity: CGFloat = 0.5

        if abs(momentumVelocityX) < minVelocity && abs(momentumVelocityY) < minVelocity {
            stopMomentumScroll()
            return
        }

        injectScrollEvent(deltaX: momentumVelocityX, deltaY: momentumVelocityY, at: lastMomentumPosition)
        momentumVelocityX *= decay
        momentumVelocityY *= decay
    }

    private func stopMomentumScroll() {
        momentumTimer?.invalidate()
        momentumTimer = nil
        momentumVelocityX = 0
        momentumVelocityY = 0
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Stop momentum scrolling
        stopMomentumScroll()

        permissionCheckTimer?.invalidate()
        statusRefreshTimer?.invalidate()

        // Stop server and cleanup
        stopServer()

        // Cancel all combine subscriptions
        cancellables.removeAll()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }
}
