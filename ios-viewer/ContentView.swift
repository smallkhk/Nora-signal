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

    // Update
    @Published var updateInfo: ReleaseInfo? = nil

    // Active tab
    @Published var tab: ViewTab = .jpeg

    // Internals
    private var relay:   RelayClient?
    private let decoder = H264Decoder()
    private let wrtc    = WebRTCClient()
    private var bag = Set<AnyCancellable>()

    enum ViewTab { case jpeg, fastcap, webrtc }

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

        // Wire WebRTC signaling → relay. Capture relay strongly via self.relay
        // at call time (not via `r` which would go weak and could go nil early).
        wrtc.emitSignal = { [weak self] event, extras in
            print("[iOS] wrtc emitSignal: \(event)")
            guard let self else { print("[iOS] wrtc emitSignal: self is nil"); return }
            guard let rc = self.relay else {
                print("[iOS] wrtc emitSignal: self.relay is nil")
                self.wrtcStatus = "no relay connection"
                return
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

            // Video area
            Group {
                switch vm.tab {
                case .jpeg:    JpegViewerPanel(vm: vm)
                case .fastcap: FastcapViewerPanel(vm: vm)
                case .webrtc:  WebrtcViewerPanel(vm: vm)
                }
            }

            // Bottom toolbar
            VStack(spacing: 0) {
                Divider().overlay(Color.gray.opacity(0.5))
                HStack(spacing: 0) {
                    TabButton(label: "JPEG", icon: "photo",
                              active: vm.tab == .jpeg) { vm.tab = .jpeg }
                    TabButton(label: "FastCap", icon: "bolt.fill",
                              active: vm.tab == .fastcap) { vm.tab = .fastcap }
                    TabButton(label: "WebRTC", icon: "waveform.path.ecg",
                              active: vm.tab == .webrtc) { vm.tab = .webrtc }
                    Spacer()
                    Button { vm.selectedAgent = nil } label: {
                        Image(systemName: "chevron.left")
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                    }
                    .foregroundColor(.white)
                }
                .background(Color.black.opacity(0.85))
            }
        }
        .statusBarHidden(true)
    }
}

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
            .padding(.horizontal, 20)
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

            // Start / Stop overlay
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

    func updateUIView(_ v: RTCMTLVideoView, context: Context) {
        // Track attachment happens in the client; nothing to update here.
    }
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
            // Try TrollStore silent install first; fall back to Safari download
            let trollURL = URL(string: "apple-magnifier://install?url=\(info.ipaURL.absoluteString)")!
            if UIApplication.shared.canOpenURL(trollURL) {
                UIApplication.shared.open(trollURL)
            } else {
                UIApplication.shared.open(info.ipaURL)
            }
        }
    }
}

// MARK: - AVSampleBufferDisplayLayer wrapper (reused from original)

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
