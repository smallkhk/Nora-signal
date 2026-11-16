//
//  RemoteInputController.swift
//  Touch → remote mouse for the iOS hub. Emits the input messages the host
//  already accepts:
//     ["t":"mouse_move","x":f,"y":f]
//     ["t":"mouse_btn","button":"left|right|middle","up":Bool,"x":f,"y":f]
//     ["t":"wheel","delta":Int,"x":f,"y":f]
//  x,y are 0..1 fractions of the host desktop. A VIRTUAL CURSOR is kept here,
//  so the host stays absolute-only and the on-screen cursor feels instant.
//
//  Setup:
//     let input = RemoteInputController(videoView: videoView, cursorView: cursor) {
//         msg in self.sendInput(msg)        // your existing UDP/ws sender
//     }
//     // whenever the video layout changes, give it the letterboxed video rect:
//     input.contentRect = computedVideoRect
//     input.mode = .trackpad                // or .direct
//
import UIKit

enum PointerMode { case trackpad, direct }

final class RemoteInputController: NSObject, UIGestureRecognizerDelegate {

    var send: ([String: Any]) -> Void
    weak var videoView: UIView?
    weak var cursorView: UIView?

    var mode: PointerMode = .trackpad
    var sensitivity: CGFloat = 1.6
    /// Letterboxed rect of the real video inside videoView. Set on layout.
    var contentRect: CGRect = .zero

    // virtual cursor, 0..1
    private var cx: CGFloat = 0.5
    private var cy: CGFloat = 0.5

    private var lastPan = CGPoint.zero
    private var lastPan2 = CGPoint.zero
    private var scrollAccum: CGFloat = 0
    private var lastTapTime: TimeInterval = 0
    private var dragLock = false
    private var holdOrigin = CGPoint.zero
    private var holdActive = false
    private var lastMoveSent: TimeInterval = 0

    init(videoView: UIView, cursorView: UIView?, send: @escaping ([String: Any]) -> Void) {
        self.videoView = videoView
        self.cursorView = cursorView
        self.send = send
        super.init()
        setupGestures()
        updateCursorView()
    }

    // MARK: geometry
    private func rect() -> CGRect {
        if contentRect != .zero { return contentRect }
        return videoView?.bounds ?? .zero
    }
    private func fraction(at p: CGPoint) -> CGPoint {
        let r = rect()
        guard r.width > 0, r.height > 0 else { return CGPoint(x: cx, y: cy) }
        return CGPoint(x: min(max((p.x - r.minX) / r.width, 0), 1),
                       y: min(max((p.y - r.minY) / r.height, 0), 1))
    }
    private func point(of f: CGPoint) -> CGPoint {
        let r = rect()
        return CGPoint(x: r.minX + f.x * r.width, y: r.minY + f.y * r.height)
    }
    private func updateCursorView() {
        cursorView?.center = point(of: CGPoint(x: cx, y: cy))
    }

    // MARK: emit
    private func sendMove(force: Bool = false) {
        let now = CACurrentMediaTime()
        if !force && now - lastMoveSent < 0.008 { updateCursorView(); return } // ~120/s cap
        lastMoveSent = now
        send(["t": "mouse_move", "x": Double(cx), "y": Double(cy)])
        updateCursorView()
    }
    private func sendBtn(_ button: String, _ up: Bool) {
        send(["t": "mouse_btn", "button": button, "up": up, "x": Double(cx), "y": Double(cy)])
    }
    private func click(_ button: String) { sendBtn(button, false); sendBtn(button, true) }

    private func moveBy(dx: CGFloat, dy: CGFloat) {
        let r = rect()
        guard r.width > 0, r.height > 0 else { return }
        cx = min(max(cx + dx / r.width, 0), 1)
        cy = min(max(cy + dy / r.height, 0), 1)
        sendMove()
    }

    // MARK: gestures
    private func setupGestures() {
        guard let v = videoView else { return }
        v.isUserInteractionEnabled = true

        let pan = UIPanGestureRecognizer(target: self, action: #selector(onPan))
        pan.maximumNumberOfTouches = 1; pan.delegate = self
        v.addGestureRecognizer(pan)

        let two = UIPanGestureRecognizer(target: self, action: #selector(onTwoPan))
        two.minimumNumberOfTouches = 2; two.maximumNumberOfTouches = 2; two.delegate = self
        v.addGestureRecognizer(two)

        let tap = UITapGestureRecognizer(target: self, action: #selector(onTap))
        tap.numberOfTouchesRequired = 1; tap.delegate = self
        v.addGestureRecognizer(tap)

        let tap2 = UITapGestureRecognizer(target: self, action: #selector(onTwoTap))
        tap2.numberOfTouchesRequired = 2; tap2.delegate = self
        v.addGestureRecognizer(tap2)

        let hold = UILongPressGestureRecognizer(target: self, action: #selector(onHold))
        hold.minimumPressDuration = 0.45; hold.delegate = self
        v.addGestureRecognizer(hold)
    }

    func gestureRecognizer(_ g: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith o: UIGestureRecognizer) -> Bool { true }

    @objc private func onTap(_ g: UITapGestureRecognizer) {
        if mode == .direct {
            let f = fraction(at: g.location(in: videoView)); cx = f.x; cy = f.y; sendMove(force: true)
        }
        click("left")
        lastTapTime = CACurrentMediaTime()
    }

    @objc private func onTwoTap(_ g: UITapGestureRecognizer) { click("right") }

    @objc private func onPan(_ g: UIPanGestureRecognizer) {
        let t = g.translation(in: videoView)
        switch g.state {
        case .began:
            lastPan = .zero
            if mode == .direct {
                let f = fraction(at: g.location(in: videoView)); cx = f.x; cy = f.y
                sendMove(force: true); sendBtn("left", false)
            } else if !holdActive, CACurrentMediaTime() - lastTapTime < 0.3 {
                dragLock = true; sendBtn("left", false)        // tap-then-drag = drag lock
            }
        case .changed:
            if mode == .direct {
                let f = fraction(at: g.location(in: videoView)); cx = f.x; cy = f.y; sendMove()
            } else {
                let dx = t.x - lastPan.x, dy = t.y - lastPan.y; lastPan = t
                moveBy(dx: dx * sensitivity, dy: dy * sensitivity)
            }
        case .ended, .cancelled:
            if mode == .direct { sendBtn("left", true) }
            else if dragLock { sendBtn("left", true); dragLock = false }
        default: break
        }
    }

    @objc private func onTwoPan(_ g: UIPanGestureRecognizer) {
        let t = g.translation(in: videoView)
        switch g.state {
        case .began: scrollAccum = 0; lastPan2 = .zero
        case .changed:
            let dy = t.y - lastPan2.y; lastPan2 = t
            scrollAccum += dy
            let step: CGFloat = 18
            while abs(scrollAccum) >= step {
                let dir: CGFloat = scrollAccum > 0 ? 1 : -1
                send(["t": "wheel", "delta": Int(-dir * 120),
                      "x": Double(cx), "y": Double(cy)])        // natural scroll
                scrollAccum -= dir * step
            }
        default: break
        }
    }

    @objc private func onHold(_ g: UILongPressGestureRecognizer) {
        guard mode == .trackpad else { return }                // press-drag (trackpad)
        switch g.state {
        case .began:
            holdOrigin = g.location(in: videoView); holdActive = true; sendBtn("left", false)
        case .changed:
            let p = g.location(in: videoView)
            let dx = p.x - holdOrigin.x, dy = p.y - holdOrigin.y; holdOrigin = p
            moveBy(dx: dx * sensitivity, dy: dy * sensitivity)
        case .ended, .cancelled:
            if holdActive { sendBtn("left", true); holdActive = false }
        default: break
        }
    }

    // Programmatic helpers (buttons / middle click / explicit scroll)
    func leftClick()  { click("left") }
    func rightClick() { click("right") }
    func middleClick(){ click("middle") }
    func centerCursor() { cx = 0.5; cy = 0.5; sendMove(force: true) }
}
