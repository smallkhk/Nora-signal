//
//  WebRTCClient.swift
//  iOS WebRTC viewer/controller. Decodes the host's H.264 with VideoToolbox
//  (hardware) and sends mouse/keyboard over the "input" datachannel using the
//  SAME messages the host expects — so RemoteInputController / RemoteKeyboardView
//  plug straight in: set their `send` to webrtc.sendInput.
//
//  Dependency (Podfile):   pod 'WebRTC-SDK'      // or 'GoogleWebRTC'
//  Signaling goes through the relay /wsignal endpoint on port 5000 — same port
//  the existing relay uses, so iOS carrier proxies don't block it.
//
//  Usage:
//     let rtc = WebRTCClient(renderer: metalVideoView)   // RTCMTLVideoView
//     rtc.connect(relay: "http://162.35.37.67:5000", session: "....", password: "....")
//     input.send      = { rtc.sendInput($0) }
//     keyboard.send   = { rtc.sendInput($0) }
//
import Foundation
import WebRTC

final class WebRTCClient: NSObject {

    // ICE must match the host. TURN is what makes frames flow on bad networks.
    private let iceServers = [
        RTCIceServer(urlStrings: ["stun:stun.l.google.com:19302"]),
        RTCIceServer(urlStrings: ["turn:162.35.37.67:3478"], username: "nora", credential: "noraturn42")
    ]

    private static let factory: RTCPeerConnectionFactory = {
        RTCInitializeSSL()
        let enc = RTCDefaultVideoEncoderFactory()
        let dec = RTCDefaultVideoDecoderFactory()          // includes H.264/VideoToolbox
        return RTCPeerConnectionFactory(encoderFactory: enc, decoderFactory: dec)
    }()

    private var pc: RTCPeerConnection?
    private var inputCh: RTCDataChannel?
    private var ws: URLSessionWebSocketTask?
    private weak var renderer: RTCVideoRenderer?
    private var remoteTrack: RTCVideoTrack?

    init(renderer: RTCVideoRenderer) {
        self.renderer = renderer
        super.init()
    }

    // MARK: signaling
    /// relay: "http://162.35.37.67:5000" — the /wsignal path is appended automatically.
    func connect(relay: String, session: String, password: String) {
        // Build ws:// URL from http:// relay base
        let wsBase = relay
            .replacingOccurrences(of: "https://", with: "wss://")
            .replacingOccurrences(of: "http://",  with: "ws://")
        let signalURL = wsBase.hasSuffix("/") ? wsBase + "wsignal" : wsBase + "/wsignal"
        let s = URLSession(configuration: .default)
        ws = s.webSocketTask(with: URL(string: signalURL)!)
        ws?.resume()
        send(json: ["type": "join", "role": "viewer", "session": session, "password": password])
        listen()
    }

    private func listen() {
        ws?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let e): print("[ws] closed:", e)
            case .success(let msg):
                if case .string(let text) = msg,
                   let data = text.data(using: .utf8),
                   let m = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    self.handle(m)
                }
                self.listen()
            }
        }
    }

    private func send(json: [String: Any]) {
        guard let d = try? JSONSerialization.data(withJSONObject: json),
              let s = String(data: d, encoding: .utf8) else { return }
        ws?.send(.string(s)) { _ in }
    }

    private func handle(_ m: [String: Any]) {
        switch m["type"] as? String {
        case "offer": if let sdp = m["sdp"] as? String { onOffer(sdp) }
        case "error": print("[sig] error:", m["msg"] ?? "")
        default: break
        }
    }

    // MARK: peer connection
    private func onOffer(_ sdp: String) {
        let cfg = RTCConfiguration()
        cfg.iceServers = iceServers
        cfg.sdpSemantics = .unifiedPlan
        let constraints = RTCMediaConstraints(mandatoryConstraints: nil, optionalConstraints: nil)
        pc = Self.factory.peerConnection(with: cfg, constraints: constraints, delegate: self)

        let remote = RTCSessionDescription(type: .offer, sdp: sdp)
        pc?.setRemoteDescription(remote) { [weak self] err in
            guard let self = self, err == nil else { print("setRemote:", err as Any); return }
            self.pc?.answer(for: constraints) { answer, err in
                guard let answer = answer, err == nil else { return }
                self.pc?.setLocalDescription(answer) { _ in
                    self.waitIceThenSendAnswer()
                }
            }
        }
    }

    // non-trickle: wait for ICE gathering to finish, then send the full SDP
    private func waitIceThenSendAnswer() {
        func check() {
            if pc?.iceGatheringState == .complete, let sdp = pc?.localDescription?.sdp {
                send(json: ["type": "answer", "sdp": sdp])
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: check)
            }
        }
        check()
    }

    // MARK: input out (feed RemoteInputController / RemoteKeyboardView here)
    func sendInput(_ msg: [String: Any]) {
        guard let ch = inputCh, ch.readyState == .open,
              let d = try? JSONSerialization.data(withJSONObject: msg) else { return }
        ch.sendData(RTCDataBuffer(data: d, isBinary: false))
    }
}

extension WebRTCClient: RTCPeerConnectionDelegate {
    func peerConnection(_ pc: RTCPeerConnection, didAdd rtpReceiver: RTCRtpReceiver,
                        streams: [RTCMediaStream]) {
        if let track = rtpReceiver.track as? RTCVideoTrack {
            remoteTrack = track
            if let r = renderer { track.add(r) }      // <-- frames render here
        }
    }
    func peerConnection(_ pc: RTCPeerConnection, didOpen dataChannel: RTCDataChannel) {
        inputCh = dataChannel                          // host created "input"
        dataChannel.delegate = self
    }
    func peerConnection(_ pc: RTCPeerConnection, didChange s: RTCIceConnectionState) {
        print("[ice]", s.rawValue)
    }
    func peerConnectionShouldNegotiate(_ pc: RTCPeerConnection) {}
    func peerConnection(_ pc: RTCPeerConnection, didChange s: RTCSignalingState) {}
    func peerConnection(_ pc: RTCPeerConnection, didRemove s: RTCMediaStream) {}
    func peerConnection(_ pc: RTCPeerConnection, didChange s: RTCIceGatheringState) {}
    func peerConnection(_ pc: RTCPeerConnection, didGenerate c: RTCIceCandidate) {}  // non-trickle: ignore
    func peerConnection(_ pc: RTCPeerConnection, didRemove c: [RTCIceCandidate]) {}
    func peerConnection(_ pc: RTCPeerConnection, didChange s: RTCPeerConnectionState) {
        print("[pc]", s.rawValue)
    }
    func peerConnection(_ pc: RTCPeerConnection, didAdd s: RTCMediaStream) {}
}

extension WebRTCClient: RTCDataChannelDelegate {
    func dataChannel(_ ch: RTCDataChannel, didChangeState s: RTCDataChannelState) {}
    func dataChannel(_ ch: RTCDataChannel, didReceiveMessageWith buffer: RTCDataBuffer) {}
}
