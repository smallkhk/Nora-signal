import Foundation
import Network
import CryptoKit

// MARK: - Packet types (must match relay + viewer.py)
private let T_HELLO_VIEW: UInt8 = 2
private let T_VIDEO:      UInt8 = 3
private let T_KEYREQ:     UInt8 = 5
private let T_PING:       UInt8 = 6

final class FastcapClient: ObservableObject {
    @Published var statusText = "Disconnected"

    var onFrame: ((Data) -> Void)?

    private var conn: NWConnection?
    private var sid   = Data(repeating: 0, count: 8)
    private var token = Data(repeating: 0, count: 8)

    private var frames:      [UInt32: (expected: Int, chunks: [Int: Data])] = [:]
    private var nextFrameId: UInt32? = nil
    private var waitingKey   = true

    private var keepAlive: DispatchSourceTimer?
    private let q = DispatchQueue(label: "fastcap.udp", qos: .userInteractive)

    // MARK: - Connect / Disconnect

    func connect(host: String, port: UInt16, session: String, password: String) {
        disconnect()

        // SID = first 8 ASCII chars of session string (matches Python viewer.py)
        let s8   = String(session.prefix(8))
        sid      = Data(s8.utf8)
        // Token = SHA-256(password + hex(sid))[:8]
        let hex  = sid.map { String(format: "%02x", $0) }.joined()
        let raw  = Data((password + hex).utf8)
        token    = Data(SHA256.hash(data: raw).prefix(8))

        let ep     = NWEndpoint.hostPort(host: NWEndpoint.Host(host),
                                          port: NWEndpoint.Port(rawValue: port)!)
        let params = NWParameters.udp
        params.allowLocalEndpointReuse = true

        conn = NWConnection(to: ep, using: params)
        conn?.stateUpdateHandler = { [weak self] state in
            DispatchQueue.main.async { self?.statusText = "\(state)" }
            if case .ready = state {
                self?.sendPkt(T_HELLO_VIEW)
                self?.startKeepalive()
                self?.recvLoop()
            }
        }
        conn?.start(queue: q)
    }

    func disconnect() {
        keepAlive?.cancel(); keepAlive = nil
        conn?.cancel();      conn      = nil
        frames.removeAll()
        nextFrameId = nil
        waitingKey  = true
        DispatchQueue.main.async { self.statusText = "Disconnected" }
    }

    // MARK: - Send

    private func sendPkt(_ type: UInt8, payload: Data = .init()) {
        var pkt = Data([type])
        pkt.append(sid)
        pkt.append(token)
        pkt.append(payload)
        conn?.send(content: pkt, completion: .idempotent)
    }

    // MARK: - Keepalive timer

    private func startKeepalive() {
        let t = DispatchSource.makeTimerSource(queue: q)
        t.schedule(deadline: .now(), repeating: 1.0)
        t.setEventHandler { [weak self] in
            self?.sendPkt(T_PING)
            if self?.waitingKey == true { self?.sendPkt(T_KEYREQ) }
        }
        t.resume()
        keepAlive = t
    }

    // MARK: - Receive loop

    private func recvLoop() {
        conn?.receiveMessage { [weak self] data, _, _, error in
            if let d = data, !d.isEmpty { self?.handlePkt(d) }
            if error == nil { self?.recvLoop() }
        }
    }

    // MARK: - Packet handling

    private func handlePkt(_ data: Data) {
        guard data.count >= 17, data[0] == T_VIDEO else { return }
        guard data[1..<9] == sid else { return }

        let fid = readU32(data, offset: 9)
        let idx = Int(readU16(data, offset: 13))
        let cnt = Int(readU16(data, offset: 15))
        let chunk = data.subdata(in: 17..<data.count)

        // Drop late packets for already-decoded frames
        if let next = nextFrameId, fid < next, next - fid < 1000 { return }

        if frames[fid] == nil { frames[fid] = (expected: cnt, chunks: [:]) }
        frames[fid]!.chunks[idx] = chunk
        guard frames[fid]!.chunks.count == cnt else { return }

        // Reassemble all fragments in order
        var assembled = Data()
        for i in 0..<cnt { assembled.append(frames[fid]!.chunks[i] ?? Data()) }

        // Evict old frames
        for old in frames.keys where old <= fid {
            if old != fid {
                if nextFrameId == nil || old >= nextFrameId! { waitingKey = true }
            }
            frames.removeValue(forKey: old)
        }
        if let next = nextFrameId, fid > next { waitingKey = true }
        nextFrameId = fid &+ 1

        if waitingKey {
            guard isKeyframe(assembled) else { return }
            waitingKey = false
        }

        onFrame?(assembled)

        if frames.count > 60 { frames.removeAll(); waitingKey = true }
    }

    // MARK: - Helpers

    private func isKeyframe(_ d: Data) -> Bool {
        let b = [UInt8](d)
        var i = 0
        while i + 3 < b.count {
            if b[i] == 0, b[i+1] == 0, b[i+2] == 1 {
                let t = b[i+3] & 0x1F
                if t == 5 || t == 7 { return true }
                i += 3
            } else { i += 1 }
        }
        return false
    }

    private func readU32(_ d: Data, offset: Int) -> UInt32 {
        d.subdata(in: offset..<offset+4).withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
    }
    private func readU16(_ d: Data, offset: Int) -> UInt16 {
        d.subdata(in: offset..<offset+2).withUnsafeBytes { $0.load(as: UInt16.self).bigEndian }
    }
}
