import AppKit
import SwiftUI

private enum OnboardingStep: Int, CaseIterable {
    case screenCapture
    case accessibility
    case ready

    var number: Int { rawValue + 1 }

    var title: String {
        switch self {
        case .screenCapture: return "Screen Capture"
        case .accessibility: return "Touch Control"
        case .ready: return "Ready"
        }
    }
}

struct TelemachusRootView: View {
    @ObservedObject var settings: DisplaySettings

    var body: some View {
        Group {
            if settings.hasCompletedOnboarding {
                SettingsView(settings: settings)
            } else {
                PermissionOnboardingView(settings: settings)
            }
        }
    }
}

struct PermissionOnboardingView: View {
    @ObservedObject var settings: DisplaySettings
    @State private var step: OnboardingStep
    @State private var screenRequestStarted = false
    @State private var accessibilityRequestStarted = false

    private var screenPermissionName: String {
        ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 26
            ? "Screen & System Audio Recording"
            : "Screen Recording"
    }

    init(settings: DisplaySettings) {
        self.settings = settings

        let initialStep: OnboardingStep
        if !settings.hasScreenRecordingPermission {
            initialStep = .screenCapture
        } else if !settings.hasAccessibilityPermission {
            initialStep = .accessibility
        } else {
            initialStep = .ready
        }
        _step = State(initialValue: initialStep)
    }

    var body: some View {
        ZStack {
            VisualEffectBlur(material: .sidebar, blendingMode: .behindWindow)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                onboardingHeader

                Divider()

                Group {
                    switch step {
                    case .screenCapture:
                        screenCaptureStep
                    case .accessibility:
                        accessibilityStep
                    case .ready:
                        readyStep
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(.horizontal, 42)
                .padding(.vertical, 32)
                .id(step)
                .transition(.asymmetric(
                    insertion: .move(edge: .trailing).combined(with: .opacity),
                    removal: .move(edge: .leading).combined(with: .opacity)
                ))

                Divider()

            HStack {
                Text("Telemachus only uses these permissions while streaming.")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Spacer()
                Link(
                    "A fork of SideScreen",
                    destination: URL(string: "https://github.com/tranvuongquocdat/SideScreen")!
                )
                    .font(.system(size: 11, weight: .medium))
            }
                .padding(.horizontal, 20)
                .frame(height: 48)
                .background(.ultraThinMaterial)
            }
        }
        .frame(width: 480, height: 780)
        .onChange(of: settings.hasScreenRecordingPermission) { granted in
            guard granted, step == .screenCapture else { return }
            withAnimation(.easeInOut(duration: 0.25)) {
                step = settings.hasAccessibilityPermission ? .ready : .accessibility
            }
        }
        .onChange(of: settings.hasAccessibilityPermission) { granted in
            guard granted, step == .accessibility else { return }
            withAnimation(.easeInOut(duration: 0.25)) {
                step = .ready
            }
        }
    }

    private var onboardingHeader: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                Image(nsImage: NSApplication.shared.applicationIconImage)
                    .resizable()
                    .interpolation(.high)
                    .frame(width: 40, height: 40)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

                VStack(alignment: .leading, spacing: 2) {
                    Text("Set up Telemachus")
                        .font(.system(size: 18, weight: .bold, design: .rounded))
                    Text("SideScreen fork · two permissions, then your tablet is ready.")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }

                Spacer()

                Text("Step \(step.number) of \(OnboardingStep.allCases.count)")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 5)
                    .background(.thinMaterial, in: Capsule())
            }

            HStack(spacing: 8) {
                ForEach(OnboardingStep.allCases, id: \.rawValue) { item in
                    VStack(alignment: .leading, spacing: 6) {
                        Capsule()
                            .fill(item.rawValue <= step.rawValue
                                  ? Color.accentColor
                                  : Color.secondary.opacity(0.18))
                            .frame(height: 3)
                        Text(item.title)
                            .font(.system(size: 10, weight: item == step ? .semibold : .regular))
                            .foregroundStyle(item.rawValue <= step.rawValue ? .primary : .secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 18)
        .background(.ultraThinMaterial)
    }

    private var screenCaptureStep: some View {
        VStack(spacing: 24) {
            permissionIcon(
                symbol: "rectangle.inset.filled.and.person.filled",
                color: .accentColor,
                granted: settings.hasScreenRecordingPermission
            )

            VStack(spacing: 9) {
                Text("Allow screen capture")
                    .font(.system(size: 24, weight: .bold, design: .rounded))
                Text("Telemachus needs \(screenPermissionName) access to send display pixels to your Android tablet.")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }

            permissionExplanation(
                rows: [
                    ("display", "Required for mirroring and extended display"),
                    ("speaker.slash.fill", "System audio is not captured"),
                    ("lock.shield.fill", "Video stays on your USB or local connection")
                ]
            )

            VStack(spacing: 10) {
                Button {
                    screenRequestStarted = true
                    settings.requestScreenRecordingPermission()
                } label: {
                    Label(
                        screenRequestStarted ? "Open \(screenPermissionName) Settings" : "Allow \(screenPermissionName)",
                        systemImage: "gear"
                    )
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)

                if screenRequestStarted && !settings.hasScreenRecordingPermission {
                    Label("Enable Telemachus in the list. macOS may ask you to quit and reopen it.", systemImage: "arrow.turn.down.left")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var accessibilityStep: some View {
        VStack(spacing: 24) {
            permissionIcon(
                symbol: "hand.tap.fill",
                color: .blue,
                granted: settings.hasAccessibilityPermission
            )

            VStack(spacing: 9) {
                HStack(spacing: 7) {
                    Text("Enable touch control")
                        .font(.system(size: 24, weight: .bold, design: .rounded))
                    Text("Optional")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(.thinMaterial, in: Capsule())
                }

                Text("Accessibility lets taps and gestures on the tablet control the Mac. Streaming works without it.")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }

            permissionExplanation(
                rows: [
                    ("cursorarrow.click.2", "Tap, scroll, drag, and pinch from the tablet"),
                    ("keyboard", "No keyboard or text contents are read"),
                    ("switch.2", "Touch control can be disabled at any time")
                ]
            )

            VStack(spacing: 10) {
                Button {
                    accessibilityRequestStarted = true
                    settings.requestAccessibilityPermission()
                } label: {
                    Label(
                        accessibilityRequestStarted ? "Open Accessibility Settings" : "Enable Touch Control",
                        systemImage: "hand.tap"
                    )
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)

                Button("Continue without touch control") {
                    settings.touchEnabled = false
                    withAnimation(.easeInOut(duration: 0.25)) {
                        step = .ready
                    }
                }
                .buttonStyle(.plain)
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
            }
        }
    }

    private var readyStep: some View {
        VStack(spacing: 24) {
            permissionIcon(symbol: "checkmark", color: .green, granted: true)

            VStack(spacing: 9) {
                Text("Ready for your tablet")
                    .font(.system(size: 24, weight: .bold, design: .rounded))
                Text("Telemachus will start over USB and open the connected Android client automatically.")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }

            VStack(spacing: 0) {
                readinessRow(
                    symbol: "rectangle.on.rectangle",
                    title: screenPermissionName,
                    status: settings.hasScreenRecordingPermission ? "Granted" : "Required",
                    color: settings.hasScreenRecordingPermission ? .green : .red
                )
                Divider().padding(.leading, 38)
                readinessRow(
                    symbol: "hand.tap",
                    title: "Touch control",
                    status: settings.hasAccessibilityPermission ? "Enabled" : "Not enabled",
                    color: settings.hasAccessibilityPermission ? .green : .secondary
                )
                Divider().padding(.leading, 38)
                readinessRow(
                    symbol: "cable.connector",
                    title: "Connection",
                    status: "USB",
                    color: .accentColor
                )
            }
            .padding(.horizontal, 14)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.08))
            }

            Button {
                if settings.connectionMode != .usb {
                    settings.connectionMode = .usb
                }
                settings.hasCompletedOnboarding = true
                if !settings.isRunning {
                    settings.toggleServer()
                }
            } label: {
                Label("Start over USB", systemImage: "play.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!settings.hasScreenRecordingPermission)

            if !settings.hasScreenRecordingPermission {
                Button("Back to screen capture") {
                    withAnimation(.easeInOut(duration: 0.25)) {
                        step = .screenCapture
                    }
                }
                .buttonStyle(.plain)
                .font(.system(size: 12))
            }
        }
    }

    private func permissionIcon(symbol: String, color: Color, granted: Bool) -> some View {
        ZStack(alignment: .bottomTrailing) {
            Circle()
                .fill(color.opacity(0.12))
                .frame(width: 78, height: 78)
                .overlay {
                    Circle()
                        .strokeBorder(color.opacity(0.18), lineWidth: 1)
                }

            Image(systemName: symbol)
                .font(.system(size: 31, weight: .medium))
                .foregroundStyle(color)
                .frame(width: 78, height: 78)

            if granted {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 22))
                    .foregroundStyle(.white, Color.green)
                    .background(Circle().fill(Color(nsColor: .windowBackgroundColor)).padding(-2))
            }
        }
    }

    private func permissionExplanation(rows: [(String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                Label {
                    Text(row.1)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                } icon: {
                    Image(systemName: row.0)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(Color.accentColor)
                        .frame(width: 18)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08))
        }
    }

    private func readinessRow(
        symbol: String,
        title: String,
        status: String,
        color: Color
    ) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(color)
                .frame(width: 18)
            Text(title)
                .font(.system(size: 12))
            Spacer()
            HStack(spacing: 5) {
                Circle()
                    .fill(color)
                    .frame(width: 6, height: 6)
                Text(status)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(color)
            }
        }
        .frame(height: 42)
    }
}
