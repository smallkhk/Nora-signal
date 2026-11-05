import Foundation
import WebRTC

// Handles one WebRTC peer connection to the SyncHost PC.
//
// Signaling protocol (matches hub.html + nora-monitor/webrtc_handler.py):
//   1. We emit `webrtc_request` → PC creates an SDP offer and emits `webrtc_offer`.
//   2. We set remote description, create answer, set local description, emit `webrtc_answer`.
//   3. We receive incoming video track and hand its RTCVideoTrack to the view.
//   4. Stop: we emit `webrtc_stop` and close the peer connection.
//
// Vanilla ICE (same as the Python side): no trickle, we wait for ICE gathering
// to complete before sending the answer.

final class WebRTCClient: NSObject {

    // MARK: - Config
    private static let iceServers = [
        RTCIceServer(urlStrings: ["stun:stun.l.google.com:19302",
                                   "stun:stun1.l.google.com:19302"]),
        // Self-hosted coturn — required when either end has symmetric NAT.
        RTCIceServer(urlStrings: ["turn:162.35.37.67:3478?transport=udp",
                                   "turn:162.35.37.67:3478?transport=tcp"],
                     username: "nora",
                     credential: "noraturn42")
    ]

    // One shared factory per process — RTCPeerConnectionFactory is expensive.
    private static let factory: RTCPeerConnectionFactory = {
        RTCInitializeSSL()
        let encoder = RTCDefaultVideoEncoderFactory()
        let decoder = RTCDefaultVideoDecoderFactory()
        return RTCPeerConnectionFactory(encoderFactory: encoder,
                                        decoderFactory: decoder)
    }()

    // MARK: - State
    private var pc: RTCPeerConnection?
    private weak var remoteRenderer: RTCVideoRenderer?
    private var remoteTrack: RTCVideoTrack?

    // Callbacks the ViewModel sets
    var onLog:             ((String) -> Void)?
    var onConnectionState: ((RTCPeerConnectionState) -> Void)?
    var onRemoteTrack:     ((RTCVideoTrack) -> Void)?

    // Signaling emit — the ViewModel wires this to the Socket.IO client.
    var emitSignal: ((String, [String: Any]) -> Void)?

    // MARK: - Lifecycle

    /// Create the peer connection and send `webrtc_request` to the PC.
    func start() {
        onLog?("start() called")
        close()  // reset any previous state

        // Touch the factory FIRST — this forces lazy init so if the WebRTC
        // framework failed to load, we see it in the log rather than silently
        // getting nil back.
        let fac = Self.factory
        onLog?("factory ready")

        let cfg = RTCConfiguration()
        cfg.iceServers       = Self.iceServers
        cfg.sdpSemantics     = .unifiedPlan
        cfg.continualGatheringPolicy = .gatherOnce

        let constraints = RTCMediaConstraints(
            mandatoryConstraints: nil,
            optionalConstraints:  ["DtlsSrtpKeyAgreement": "true"])

        guard let newPc = fac.peerConnection(with: cfg,
                                              constraints: constraints,
                                              delegate: self) else {
            onLog?("peerConnection() returned nil")
            return
        }
        pc = newPc
        onLog?("peerConnection created")

        // We only RECEIVE video from the PC — set recv-only transceiver.
        let transceiverInit = RTCRtpTransceiverInit()
        transceiverInit.direction = .recvOnly
        _ = newPc.addTransceiver(of: .video, init: transceiverInit)
        onLog?("transceiver added")

        if emitSignal == nil {
            onLog?("ERROR: emitSignal callback not wired")
            return
        }
        emitSignal?("webrtc_request", [:])
        onLog?("webrtc_request emitted")
    }

    /// Call when a `webrtc_offer` arrives from the PC.
    func handleOffer(sdp: String, type: String) {
        guard let pc = pc else {
            onLog?("WebRTC: offer received but no peer connection")
            return
        }
        let desc = RTCSessionDescription(type: Self.sdpType(from: type), sdp: sdp)
        pc.setRemoteDescription(desc) { [weak self] err in
            guard let self else { return }
            if let err {
                self.onLog?("WebRTC: setRemoteDescription failed: \(err)")
                return
            }
            self.createAndSendAnswer()
        }
    }

    private func createAndSendAnswer() {
        guard let pc = pc else { return }
        let constraints = RTCMediaConstraints(mandatoryConstraints: nil,
                                               optionalConstraints: nil)
        pc.answer(for: constraints) { [weak self] sdp, err in
            guard let self, let sdp else {
                self?.onLog?("WebRTC: createAnswer failed: \(err?.localizedDescription ?? "nil sdp")")
                return
            }
            pc.setLocalDescription(sdp) { [weak self] err in
                guard let self else { return }
                if let err {
                    self.onLog?("WebRTC: setLocalDescription failed: \(err)")
                    return
                }
                // Wait for ICE gathering to complete (vanilla ICE).
                self.waitForIceComplete { [weak self] in
                    guard let self, let localDesc = self.pc?.localDescription else { return }
                    self.onLog?("WebRTC: emitting webrtc_answer (sdp \(localDesc.sdp.count) bytes)")
                    self.emitSignal?("webrtc_answer", [
                        "sdp":  localDesc.sdp,
                        "type": Self.sdpTypeString(localDesc.type)
                    ])
                }
            }
        }
    }

    private func waitForIceComplete(timeoutSec: Double = 5.0, done: @escaping () -> Void) {
        guard let pc = pc else { done(); return }
        if pc.iceGatheringState == .complete { done(); return }
        // Poll — the delegate also notifies but polling keeps the code simple.
        let deadline = DispatchTime.now() + timeoutSec
        func check() {
            if self.pc?.iceGatheringState == .complete {
                done(); return
            }
            if DispatchTime.now() >= deadline {
                done(); return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { check() }
        }
        check()
    }

    /// Attach a video renderer. If the remote track is already available, binds now;
    /// otherwise binds on `ontrack`.
    func attachRenderer(_ renderer: RTCVideoRenderer) {
        remoteRenderer = renderer
        if let track = remoteTrack {
            track.add(renderer)
        }
    }

    func stop() {
        onLog?("WebRTC: stopping")
        emitSignal?("webrtc_stop", [:])
        close()
    }

    func close() {
        if let track = remoteTrack, let renderer = remoteRenderer {
            track.remove(renderer)
        }
        remoteTrack = nil
        pc?.close()
        pc = nil
    }

    // MARK: - Helpers
    private static func sdpType(from s: String) -> RTCSdpType {
        switch s.lowercased() {
        case "offer":    return .offer
        case "answer":   return .answer
        case "pranswer": return .prAnswer
        default:         return .rollback
        }
    }

    private static func sdpTypeString(_ t: RTCSdpType) -> String {
        switch t {
        case .offer:    return "offer"
        case .answer:   return "answer"
        case .prAnswer: return "pranswer"
        case .rollback: return "rollback"
        @unknown default: return "answer"
        }
    }
}

// MARK: - RTCPeerConnectionDelegate
extension WebRTCClient: RTCPeerConnectionDelegate {
    func peerConnection(_ pc: RTCPeerConnection, didChange newState: RTCSignalingState)        {}
    func peerConnection(_ pc: RTCPeerConnection, didAdd stream: RTCMediaStream)                {}
    func peerConnection(_ pc: RTCPeerConnection, didRemove stream: RTCMediaStream)             {}
    func peerConnectionShouldNegotiate(_ pc: RTCPeerConnection)                                {}
    func peerConnection(_ pc: RTCPeerConnection, didChange newState: RTCIceGatheringState)     {}
    func peerConnection(_ pc: RTCPeerConnection, didGenerate candidate: RTCIceCandidate)       {}
    func peerConnection(_ pc: RTCPeerConnection, didRemove candidates: [RTCIceCandidate])      {}
    func peerConnection(_ pc: RTCPeerConnection, didOpen dataChannel: RTCDataChannel)          {}

    func peerConnection(_ pc: RTCPeerConnection, didChange newState: RTCIceConnectionState) {
        onLog?("WebRTC: iceConnectionState=\(newState.rawValue)")
    }

    func peerConnection(_ pc: RTCPeerConnection, didChange newState: RTCPeerConnectionState) {
        onLog?("WebRTC: connectionState=\(newState.rawValue)")
        DispatchQueue.main.async { [weak self] in
            self?.onConnectionState?(newState)
        }
    }

    func peerConnection(_ pc: RTCPeerConnection, didAdd rtpReceiver: RTCRtpReceiver,
                        streams: [RTCMediaStream]) {
        guard let track = rtpReceiver.track as? RTCVideoTrack else { return }
        onLog?("WebRTC: remote video track received")
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.remoteTrack = track
            if let renderer = self.remoteRenderer {
                track.add(renderer)
            }
            self.onRemoteTrack?(track)
        }
    }
}
