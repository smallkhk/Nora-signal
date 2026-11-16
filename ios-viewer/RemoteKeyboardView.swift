//
//  RemoteKeyboardView.swift
//  Touch keyboard → remote keystrokes for the iOS hub. Emits:
//     ["t":"key","vk":Int,"up":Bool]       (Windows virtual-key codes)
//
//  Soft keyboard gives characters, not key events, so each char is mapped to a
//  VK (+ shift when needed) and sent as down+up. Modifier keys (Ctrl/Alt/Win/
//  Shift) are sticky toggles applied to the next key; Esc/Tab/arrows/F-keys come
//  from the accessory bar. iPad hardware keyboards go through pressesBegan/Ended.
//
//  Setup:
//     let kb = RemoteKeyboardView()
//     kb.send = { msg in self.sendInput(msg) }
//     view.addSubview(kb)                    // zero-size, offscreen is fine
//     // show/hide with a toolbar button:
//     kb.show()    // becomes first responder, raises the keyboard + accessory bar
//
import UIKit

final class RemoteKeyboardView: UIView, UIKeyInput {

    var send: ([String: Any]) -> Void = { _ in }

    // sticky modifiers (one-shot unless locked by double-tap)
    private var ctrl = false, alt = false, win = false, shiftLock = false
    private var ctrlLocked = false, altLocked = false, winLocked = false

    // MARK: first responder / UIKeyInput
    override var canBecomeFirstResponder: Bool { true }
    var hasText: Bool { true }

    func show() { becomeFirstResponder() }
    func hide() { resignFirstResponder() }

    func insertText(_ text: String) { for ch in text { sendChar(ch) } }
    func deleteBackward() { tapVK(0x08) }

    // MARK: key emit
    private func key(_ vk: Int, _ up: Bool) { send(["t": "key", "vk": vk, "up": up]) }

    private func activeMods() -> [Int] {
        var m = [Int]()
        if ctrl { m.append(0x11) }
        if alt  { m.append(0x12) }
        if win  { m.append(0x5B) }
        if shiftLock { m.append(0x10) }
        return m
    }

    /// Press vk wrapped in the active modifiers, then auto-release one-shot ones.
    func press(_ vk: Int, extraShift: Bool = false) {
        var mods = activeMods()
        if extraShift && !shiftLock { mods.append(0x10) }
        for m in mods { key(m, false) }
        key(vk, false); key(vk, true)
        for m in mods.reversed() { key(m, true) }
        if !ctrlLocked { ctrl = false }
        if !altLocked  { alt = false }
        if !winLocked  { win = false }
        refreshBar()
    }
    func tapVK(_ vk: Int) { press(vk) }

    private func sendChar(_ ch: Character) {
        if ch == "\n" { press(0x0D); return }
        guard let (vk, needShift) = Self.vk(for: ch) else { return }
        press(vk, extraShift: needShift)
    }

    // MARK: hardware keyboard (iPad)
    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var handled = false
        for p in presses { if let k = p.key, handleHardware(k, up: false) { handled = true } }
        if !handled { super.pressesBegan(presses, with: event) }
    }
    override func pressesEnded(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        for p in presses { if let k = p.key { _ = handleHardware(k, up: true) } }
        super.pressesEnded(presses, with: event)
    }
    private func handleHardware(_ k: UIKey, up: Bool) -> Bool {
        guard let vk = Self.vk(forKeyCode: k.keyCode) else { return false }
        key(vk, up); return true
    }

    // MARK: sticky modifier controls (wire to the accessory bar)
    func toggleCtrl()  { if ctrl && !ctrlLocked { ctrlLocked = true } else if ctrlLocked { ctrl = false; ctrlLocked = false } else { ctrl = true }; refreshBar() }
    func toggleAlt()   { if alt && !altLocked { altLocked = true } else if altLocked { alt = false; altLocked = false } else { alt = true }; refreshBar() }
    func toggleWin()   { if win && !winLocked { winLocked = true } else if winLocked { win = false; winLocked = false } else { win = true }; refreshBar() }
    func toggleShift() { shiftLock.toggle(); refreshBar() }
    func special(_ vk: Int) { press(vk) }
    func ctrlAltDel()  { key(0x11,false); key(0x12,false); key(0x2E,false); key(0x2E,true); key(0x12,true); key(0x11,true) }

    // MARK: accessory bar
    private weak var barCtrl: UIButton?, barAlt: UIButton?, barWin: UIButton?, barShift: UIButton?

    override var inputAccessoryView: UIView? {
        let bar = UIScrollView()
        bar.frame = CGRect(x: 0, y: 0, width: UIScreen.main.bounds.width, height: 44)
        bar.backgroundColor = UIColor(white: 0.12, alpha: 1)
        let stack = UIStackView(); stack.axis = .horizontal; stack.spacing = 6
        stack.translatesAutoresizingMaskIntoConstraints = false

        func mk(_ title: String, _ action: @escaping () -> Void) -> UIButton {
            let b = UIButton(type: .system)
            b.setTitle(title, for: .normal); b.setTitleColor(.white, for: .normal)
            b.backgroundColor = UIColor(white: 0.25, alpha: 1)
            b.titleLabel?.font = .systemFont(ofSize: 15, weight: .medium)
            b.contentEdgeInsets = UIEdgeInsets(top: 6, left: 12, bottom: 6, right: 12)
            b.layer.cornerRadius = 6
            b.addAction(UIAction { _ in action() }, for: .touchUpInside)
            return b
        }
        let c = mk("Ctrl") { [weak self] in self?.toggleCtrl() }; barCtrl = c
        let a = mk("Alt")  { [weak self] in self?.toggleAlt() };  barAlt = a
        let w = mk("Win")  { [weak self] in self?.toggleWin() };  barWin = w
        let s = mk("Shift"){ [weak self] in self?.toggleShift() };barShift = s
        let items: [UIButton] = [
            c, a, w, s,
            mk("Esc"){ [weak self] in self?.special(0x1B) },
            mk("Tab"){ [weak self] in self?.special(0x09) },
            mk("↑"){ [weak self] in self?.special(0x26) },
            mk("↓"){ [weak self] in self?.special(0x28) },
            mk("←"){ [weak self] in self?.special(0x25) },
            mk("→"){ [weak self] in self?.special(0x27) },
            mk("Ctrl+Alt+Del"){ [weak self] in self?.ctrlAltDel() }
        ]
        var fkeys = [UIButton]()
        for i in 1...12 { let n = i; fkeys.append(mk("F\(n)") { [weak self] in self?.special(0x70 + n - 1) }) }
        (items + fkeys).forEach { stack.addArrangedSubview($0) }

        bar.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 8),
            stack.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -8),
            stack.topAnchor.constraint(equalTo: bar.topAnchor, constant: 5),
            stack.bottomAnchor.constraint(equalTo: bar.bottomAnchor, constant: -5),
            stack.heightAnchor.constraint(equalToConstant: 34)
        ])
        return bar
    }

    private func refreshBar() {
        func style(_ b: UIButton?, _ on: Bool, _ lock: Bool) {
            b?.backgroundColor = lock ? UIColor.systemOrange
                : (on ? UIColor.systemBlue : UIColor(white: 0.25, alpha: 1))
        }
        style(barCtrl, ctrl, ctrlLocked); style(barAlt, alt, altLocked)
        style(barWin, win, winLocked); style(barShift, shiftLock, false)
    }

    // MARK: char -> (vk, needShift)
    static func vk(for ch: Character) -> (Int, Bool)? {
        if let a = ch.asciiValue {
            if a >= 65 && a <= 90 { return (Int(a), true) }        // A-Z
            if a >= 97 && a <= 122 { return (Int(a - 32), false) } // a-z -> VK of upper
            if a >= 48 && a <= 57 { return (Int(a), false) }       // 0-9
            if ch == " " { return (0x20, false) }
        }
        let unshifted: [Character: Int] = [
            ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
            "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE]
        if let vk = unshifted[ch] { return (vk, false) }
        let shifted: [Character: Int] = [
            ":": 0xBA, "+": 0xBB, "<": 0xBC, "_": 0xBD, ">": 0xBE, "?": 0xBF,
            "~": 0xC0, "{": 0xDB, "|": 0xDC, "}": 0xDD, "\"": 0xDE,
            "!": 0x31, "@": 0x32, "#": 0x33, "$": 0x34, "%": 0x35,
            "^": 0x36, "&": 0x37, "*": 0x38, "(": 0x39, ")": 0x30]
        if let vk = shifted[ch] { return (vk, true) }
        return nil
    }

    // MARK: hardware keyCode -> vk (common keys)
    static func vk(forKeyCode code: UIKeyboardHIDUsage) -> Int? {
        switch code {
        case .keyboardReturnOrEnter, .keypadEnter: return 0x0D
        case .keyboardEscape: return 0x1B
        case .keyboardDeleteOrBackspace: return 0x08
        case .keyboardTab: return 0x09
        case .keyboardSpacebar: return 0x20
        case .keyboardLeftArrow: return 0x25
        case .keyboardRightArrow: return 0x27
        case .keyboardUpArrow: return 0x26
        case .keyboardDownArrow: return 0x28
        case .keyboardDeleteForward: return 0x2E
        case .keyboardHome: return 0x24
        case .keyboardEnd: return 0x23
        case .keyboardPageUp: return 0x21
        case .keyboardPageDown: return 0x22
        default:
            let raw = code.rawValue
            if raw >= UIKeyboardHIDUsage.keyboardA.rawValue, raw <= UIKeyboardHIDUsage.keyboardZ.rawValue {
                return 0x41 + Int(raw - UIKeyboardHIDUsage.keyboardA.rawValue)   // A-Z
            }
            if raw >= UIKeyboardHIDUsage.keyboard1.rawValue, raw <= UIKeyboardHIDUsage.keyboard9.rawValue {
                return 0x31 + Int(raw - UIKeyboardHIDUsage.keyboard1.rawValue)   // 1-9
            }
            if code == .keyboard0 { return 0x30 }
            if raw >= UIKeyboardHIDUsage.keyboardF1.rawValue, raw <= UIKeyboardHIDUsage.keyboardF12.rawValue {
                return 0x70 + Int(raw - UIKeyboardHIDUsage.keyboardF1.rawValue) // F1-F12
            }
            return nil
        }
    }
}
