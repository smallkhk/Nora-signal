import SwiftUI
import AVFoundation
import Combine

// MARK: - App entry point

@main
struct FastcapViewerApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}

// MARK: - ViewModel

final class ViewerVM: ObservableObject {
    @Published var host     = "162.35.37.67"
    @Published var portStr  = "5000"
    @Published var session  = ""
    @Published var password = ""
    @Published var status   = ""
    @Published var frame:   CVPixelBuffer? = nil
    @Published var connected = false

    private let client  = FastcapClient()
    private let decoder = H264Decoder()
    private var bag     = Set<AnyCancellable>()

    init() {
        decoder.onFrame = { [weak self] pb in
            DispatchQueue.main.async { self?.frame = pb }
        }
        client.onFrame = { [weak self] annexB in
            self?.decoder.feed(annexB: annexB)
        }
        client.$statusText
            .receive(on: DispatchQueue.main)
            .assign(to: \.status, on: self)
            .store(in: &bag)
    }

    func connect() {
        client.connect(host: host,
                       port: UInt16(portStr) ?? 5000,
                       session: session,
                       password: password)
        connected = true
    }

    func disconnect() {
        client.disconnect()
        connected = false
        frame = nil
    }
}

// MARK: - Root view

struct ContentView: View {
    @StateObject private var vm = ViewerVM()

    var body: some View {
        if vm.connected {
            VideoScreen(vm: vm)
        } else {
            NavigationStack {
                LoginScreen(vm: vm)
                    .navigationTitle("FastCap Viewer")
            }
        }
    }
}

// MARK: - Login screen

struct LoginScreen: View {
    @ObservedObject var vm: ViewerVM

    var body: some View {
        Form {
            Section("Relay server") {
                TextField("Host", text: $vm.host)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                    .keyboardType(.URL)
                TextField("Port", text: $vm.portStr)
                    .keyboardType(.numberPad)
            }
            Section("Session") {
                TextField("Session ID (8 chars)", text: $vm.session)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                SecureField("Password", text: $vm.password)
            }
            Section {
                Button(action: vm.connect) {
                    Label("Connect", systemImage: "play.fill")
                }
                .disabled(vm.session.count < 8 || vm.password.isEmpty)
                if !vm.status.isEmpty {
                    Text(vm.status).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

// MARK: - Video screen

struct VideoScreen: View {
    @ObservedObject var vm: ViewerVM

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color.black.ignoresSafeArea()

            if let frame = vm.frame {
                PixelBufferView(pixelBuffer: frame)
                    .ignoresSafeArea()
            } else {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Waiting for host…")
                        .foregroundStyle(.white)
                        .font(.headline)
                    Text(vm.status).font(.caption).foregroundStyle(.gray)
                }
            }

            Button {
                vm.disconnect()
            } label: {
                Image(systemName: "xmark")
                    .padding(10)
                    .background(.black.opacity(0.6))
                    .clipShape(Circle())
                    .foregroundColor(.white)
            }
            .padding()
        }
        .statusBarHidden(true)
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
