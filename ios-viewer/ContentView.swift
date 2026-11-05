import SwiftUI
import AVFoundation
import Combine
import UIKit

// MARK: - App entry point

@main
struct NoraViewerApp: App {
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

    // Update
    @Published var updateInfo: ReleaseInfo? = nil

    // Active tab
    @Published var tab: ViewTab = .jpeg

    // Internals
    private var relay:   RelayClient?
    private let decoder = H264Decoder()
    private var bag = Set<AnyCancellable>()

    enum ViewTab { case jpeg, fastcap }

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

        relay = r
        r.connect()

        checkForUpdate()
    }

    func disconnect() {
        stopJpeg()
        stopFastcap()
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
        relay?.joinFastcap()
        statusMsg = "Joining FastCap…"
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
                if vm.tab == .jpeg {
                    JpegViewerPanel(vm: vm)
                } else {
                    FastcapViewerPanel(vm: vm)
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
                Text("Tap to download IPA")
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
            UIApplication.shared.open(info.ipaURL)
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
