import Foundation

// Minimal Socket.IO v4 (EIO4) client over WebSocket.
// Supports: connect, emit, on, off, disconnect.
// Does NOT use SocketIO pods — pure URLSessionWebSocketTask.

final class SocketIOClient {

    // MARK: - Types

    enum State { case disconnected, connecting, connected }

    typealias Handler = ([Any]) -> Void

    // MARK: - Public state

    private(set) var state: State = .disconnected
    var onConnect:    (() -> Void)?
    var onDisconnect: ((Error?) -> Void)?

    // MARK: - Private

    private var url: URL
    private var task: URLSessionWebSocketTask?
    private let session = URLSession(configuration: .default)
    private var handlers: [String: [Handler]] = [:]
    private var lock = NSLock()
    private var sid = ""
    private var pingTimer: Timer?
    private var reconnectTimer: Timer?
    private var shouldReconnect = false
    private var pingInterval: TimeInterval = 25
    private var reconnectDelay: TimeInterval = 3

    // MARK: - Init

    init(url: URL) {
        self.url = url
    }

    // MARK: - Connect / Disconnect

    func connect() {
        guard state == .disconnected else { return }
        state = .connecting
        shouldReconnect = true
        _open()
    }

    func disconnect() {
        shouldReconnect = false
        _close(error: nil)
    }

    // MARK: - Emit

    func emit(_ event: String, _ items: Any...) {
        guard state == .connected else { return }
        let payload: [Any] = [event] + items
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let str  = String(data: data, encoding: .utf8) else { return }
        _send("42" + str)
    }

    // MARK: - On / Off

    func on(_ event: String, handler: @escaping Handler) {
        lock.lock(); defer { lock.unlock() }
        handlers[event, default: []].append(handler)
    }

    func off(_ event: String) {
        lock.lock(); defer { lock.unlock() }
        handlers.removeValue(forKey: event)
    }

    // MARK: - Private impl

    private func _open() {
        // EIO4 handshake query
        var comps = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        comps.queryItems = (comps.queryItems ?? []) + [
            .init(name: "EIO",       value: "4"),
            .init(name: "transport", value: "websocket"),
        ]
        let req = URLRequest(url: comps.url!)
        task = session.webSocketTask(with: req)
        task?.resume()
        _recv()
    }

    private func _close(error: Error?) {
        pingTimer?.invalidate(); pingTimer = nil
        reconnectTimer?.invalidate(); reconnectTimer = nil
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        let prev = state
        state = .disconnected
        sid = ""
        if prev != .disconnected {
            DispatchQueue.main.async { self.onDisconnect?(error) }
        }
        if shouldReconnect {
            reconnectTimer = Timer.scheduledTimer(withTimeInterval: reconnectDelay, repeats: false) { [weak self] _ in
                guard let s = self, s.shouldReconnect else { return }
                s.state = .connecting
                s._open()
            }
        }
    }

    private func _send(_ text: String) {
        task?.send(.string(text)) { _ in }
    }

    private func _recv() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let err):
                self._close(error: err)
            case .success(let msg):
                switch msg {
                case .string(let s):  self._handle(s)
                case .data:           break
                @unknown default:     break
                }
                self._recv()
            }
        }
    }

    // MARK: - EIO/SIO packet parsing

    // EIO packet types: 0=OPEN,1=CLOSE,2=PING,3=PONG,4=MESSAGE,5=UPGRADE,6=NOOP
    // SIO (over EIO4=MESSAGE): 0=CONNECT,1=DISCONNECT,2=EVENT,3=ACK

    private func _handle(_ text: String) {
        guard !text.isEmpty else { return }
        let eio = text.first!
        let rest = String(text.dropFirst())

        switch eio {
        case "0":   // EIO OPEN — parse sid & pingInterval
            if let d = rest.data(using: .utf8),
               let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
                sid = j["sid"] as? String ?? ""
                if let pi = j["pingInterval"] as? Double { pingInterval = pi / 1000 }
            }
            _send("40")     // SIO CONNECT to default namespace
            _startPing()

        case "1":   // EIO CLOSE
            _close(error: nil)

        case "2":   // EIO PING (server→client)
            _send("3")      // PONG

        case "4":   // EIO MESSAGE → SIO
            guard !rest.isEmpty else { return }
            let sio = rest.first!
            let payload = String(rest.dropFirst())
            switch sio {
            case "0":   // SIO CONNECT
                state = .connected
                DispatchQueue.main.async { self.onConnect?() }
            case "1":   // SIO DISCONNECT
                _close(error: nil)
            case "2":   // SIO EVENT
                _dispatch(payload)
            default: break
            }

        default: break
        }
    }

    private func _dispatch(_ payload: String) {
        guard let d = payload.data(using: .utf8),
              let arr = try? JSONSerialization.jsonObject(with: d) as? [Any],
              let event = arr.first as? String else { return }
        let args = Array(arr.dropFirst())
        lock.lock()
        let hs = handlers[event] ?? []
        lock.unlock()
        DispatchQueue.main.async { hs.forEach { $0(args) } }
    }

    private func _startPing() {
        pingTimer?.invalidate()
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.pingTimer = Timer.scheduledTimer(withTimeInterval: self.pingInterval,
                                                  repeats: true) { [weak self] _ in
                self?._send("2")    // EIO PING
            }
        }
    }
}
