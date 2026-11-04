import Foundation
import UIKit

// Connects to the Nora relay server via Socket.IO v4.
// Handles: agent list, JPEG frames, fastcap_info, screen stream control.

final class RelayClient: ObservableObject {

    // MARK: - Published state

    @Published var agents:       [String] = []
    @Published var connected     = false
    @Published var selectedAgent: String? = nil

    // Callbacks set by ViewModel
    var onJpegFrame:    ((Data) -> Void)?
    var onFastcapInfo:  ((_ host: String, _ port: UInt16, _ session: String, _ password: String) -> Void)?
    var onLog:          ((String) -> Void)?

    // MARK: - Private

    private var sio: SocketIOClient
    private var relayURL: URL
    private var streaming = false

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
        // Join as hub viewer
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

    func requestFastcap() {
        guard let agent = selectedAgent else { return }
        sio.emit("fastcap_start", ["_target": agent])
        onLog?("fastcap_start → \(agent)")
    }

    func stopFastcap() {
        guard let agent = selectedAgent else { return }
        sio.emit("fastcap_stop", ["_target": agent])
        onLog?("fastcap_stop → \(agent)")
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

        // Single agent joined
        sio.on("agent_joined") { [weak self] args in
            guard let self,
                  let d    = args.first as? [String: Any],
                  let name = d["name"] as? String else { return }
            if !self.agents.contains(name) {
                self.agents.append(name)
                self.onLog?("Agent joined: \(name)")
            }
        }

        // Agent left
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

        // FastCap info (session/password from host)
        sio.on("fastcap_info") { [weak self] args in
            guard let self,
                  let d   = args.first as? [String: Any],
                  let sess = d["session"]  as? String,
                  let pass = d["password"] as? String else { return }
            // The relay UDP host/port — same relay IP, port 5000
            var comps = URLComponents(url: self.relayURL, resolvingAgainstBaseURL: false)!
            let host  = comps.host ?? "162.35.37.67"
            let port  = UInt16(comps.port ?? 5000)
            self.onLog?("fastcap_info session=\(sess.prefix(8))…")
            self.onFastcapInfo?(host, port, sess, pass)
        }
    }
}
