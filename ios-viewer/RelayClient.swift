import Foundation
import UIKit

// Connects to the Nora relay server via Socket.IO v4.
// FastCap video arrives via Socket.IO fastcap_video events (no direct UDP).

final class RelayClient: ObservableObject {

    // MARK: - Published state

    @Published var agents:       [String] = []
    @Published var connected     = false
    @Published var selectedAgent: String? = nil

    // Callbacks set by ViewModel
    var onJpegFrame:    ((Data) -> Void)?
    var onFastcapFrame: ((Data) -> Void)?   // H.264 annex-B
    var onFastcapReady: (() -> Void)?
    var onLog:          ((String) -> Void)?

    // MARK: - Private

    private var sio: SocketIOClient
    private var relayURL: URL
    private var streaming = false
    private var fcSession  = ""
    private var fcPassword = ""

    // MARK: - Init

    init(relayURL: URL) {
        self.relayURL = relayURL
        sio = SocketIOClient(url: relayURL)
        _setup()
    }

    // MARK: - Connect / Disconnect

    func connect() {
        sio.connect()
    }

    func disconnect() {
        sio.disconnect()
        DispatchQueue.main.async {
            self.connected     = false
            self.agents        = []
            self.selectedAgent = nil
        }
    }

    // MARK: - Agent selection

    func selectAgent(_ name: String) {
        selectedAgent = name
        sio.emit("hub_join", ["role": "hub"])
        onLog?("Selected agent: \(name)")
    }

    // MARK: - Screen stream control

    func startJpegStream() {
        guard let agent = selectedAgent else { return }
        streaming = true
        sio.emit("start_screen_stream", ["_target": agent])
        onLog?("start_screen_stream → \(agent)")
    }

    func stopJpegStream() {
        guard let agent = selectedAgent else { return }
        streaming = false
        sio.emit("stop_screen_stream", ["_target": agent])
        onLog?("stop_screen_stream → \(agent)")
    }

    // MARK: - FastCap via Socket.IO

    func joinFastcap() {
        guard !fcSession.isEmpty else {
            onLog?("No fastcap session yet — waiting for agent")
            return
        }
        sio.emit("fastcap_join", ["session": fcSession, "password": fcPassword])
        onLog?("fastcap_join session=\(String(fcSession.prefix(8)))…")
    }

    func stopFastcap() {
        // Relay times out the viewer automatically; nothing to send
    }

    // MARK: - Private setup

    private func _setup() {
        sio.onConnect = { [weak self] in
            guard let self else { return }
            self.connected = true
            self.onLog?("Socket.IO connected")
            self.sio.emit("hub_join", ["role": "hub"])
        }

        sio.onDisconnect = { [weak self] err in
            guard let self else { return }
            self.connected = false
            self.agents    = []
            self.onLog?("Socket.IO disconnected: \(err?.localizedDescription ?? "none")")
        }

        // Agent list — relay sends [{"name": "AgentName"}, ...]
        sio.on("agent_list") { [weak self] args in
            guard let self,
                  let first = args.first,
                  let list  = first as? [[String: Any]] else { return }
            let names = list.compactMap { $0["name"] as? String }
            self.agents = names
            self.onLog?("Agents: \(names.joined(separator: ", "))")
        }

        sio.on("agent_joined") { [weak self] args in
            guard let self,
                  let d    = args.first as? [String: Any],
                  let name = d["name"] as? String else { return }
            if !self.agents.contains(name) {
                self.agents.append(name)
                self.onLog?("Agent joined: \(name)")
            }
        }

        sio.on("agent_left") { [weak self] args in
            guard let self,
                  let d    = args.first as? [String: Any],
                  let name = d["name"] as? String else { return }
            self.agents.removeAll { $0 == name }
            if self.selectedAgent == name { self.selectedAgent = nil }
            self.onLog?("Agent left: \(name)")
        }

        // JPEG screen frame
        sio.on("screen_frame") { [weak self] args in
            guard let self,
                  let d   = args.first as? [String: Any],
                  let b64 = d["data"] as? String,
                  let img = Data(base64Encoded: b64) else { return }
            self.onJpegFrame?(img)
        }

        // FastCap info — store session/password; relay re-delivers on hub_join
        sio.on("fastcap_info") { [weak self] args in
            guard let self,
                  let d    = args.first as? [String: Any],
                  let sess = d["session"]  as? String,
                  let pass = d["password"] as? String else { return }
            self.fcSession  = sess
            self.fcPassword = pass
            self.onLog?("fastcap_info session=\(sess.prefix(8))…")
        }

        // Relay confirmed fastcap host is alive
        sio.on("fastcap_ready") { [weak self] args in
            guard let self,
                  let d    = args.first as? [String: Any],
                  let host = d["host"] as? Bool, host else { return }
            self.onFastcapReady?()
            self.onLog?("fastcap_ready: host alive")
        }

        // H.264 video via relay — b64 field: base64([keyFlag(1 byte)] + annexB)
        sio.on("fastcap_video") { [weak self] args in
            guard let self,
                  let d   = args.first as? [String: Any],
                  let b64 = d["b64"] as? String,
                  let raw = Data(base64Encoded: b64, options: .ignoreUnknownCharacters),
                  raw.count > 1 else { return }
            self.onFastcapFrame?(Data(raw.dropFirst()))
        }
    }
}
