"""Viewer — run on your own PC to view + control the host.
Usage:  python viewer.py YOUR_VPS_IP:5000 SESSION_ID PASSWORD

Get SESSION_ID and PASSWORD from:  curl http://YOUR_VPS_IP:5000/api/fc_pw
SESSION_ID is the first 8 chars of the "session" field.
"""
import socket
import struct
import sys
import time

import pygame

import fastcap

T_HELLO_VIEW, T_VIDEO, T_INPUT, T_KEYREQ, T_PING = 2, 3, 4, 5, 6

if len(sys.argv) < 4:
    sys.exit(__doc__)
relay_host, relay_port = sys.argv[1].rsplit(":", 1)
RELAY = (relay_host, int(relay_port))
SESSION = sys.argv[2][:8]
SID = SESSION.encode().ljust(8)[:8]
TOKEN = fastcap.make_token(SESSION, sys.argv[3])

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
sock.setblocking(False)


def send(t, payload=b""):
    # New relay (port 5000) requires token in HELLO_VIEW too
    head = bytes([t]) + SID + TOKEN
    try:
        sock.sendto(head + payload, RELAY)
    except OSError:
        pass


def is_keyframe(data):
    i = data.find(b"\x00\x00\x01")
    while i != -1 and i + 3 < len(data):
        if data[i + 3] & 0x1F in (5, 7):  # IDR slice or SPS
            return True
        i = data.find(b"\x00\x00\x01", i + 3)
    return False


SPECIAL = {
    pygame.K_RETURN: 1, pygame.K_KP_ENTER: 1, pygame.K_BACKSPACE: 2, pygame.K_TAB: 3,
    pygame.K_ESCAPE: 4, pygame.K_LEFT: 5, pygame.K_RIGHT: 6, pygame.K_UP: 7,
    pygame.K_DOWN: 8, pygame.K_DELETE: 9, pygame.K_HOME: 10, pygame.K_END: 11,
    pygame.K_PAGEUP: 12, pygame.K_PAGEDOWN: 13, pygame.K_LCTRL: 14, pygame.K_RCTRL: 14,
    pygame.K_LSHIFT: 15, pygame.K_RSHIFT: 15, pygame.K_LALT: 16, pygame.K_RALT: 16,
    pygame.K_LGUI: 17, pygame.K_RGUI: 17,
}
BUTTONS = {1: 0, 2: 2, 3: 1}  # pygame -> L, M, R

pygame.init()
info = pygame.display.Info()
screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
pygame.display.set_caption(f"Remote {SESSION} — connecting…")
pygame.key.set_repeat(0)

dec = fastcap.Decoder()
frames = {}            # frame_id -> [count, {idx: bytes}]
next_id = None         # next frame id we expect to decode
waiting_key = True
last_keyreq = 0.0
last_ping = 0.0
surface = None
frame_size = None
fps_count, fps_t = 0, time.time()

send(T_HELLO_VIEW)
clock = pygame.time.Clock()
running = True

while running:
    now = time.time()
    if now - last_ping > 1:
        send(T_PING)
        last_ping = now
    if waiting_key and now - last_keyreq > 0.5:
        send(T_KEYREQ)
        last_keyreq = now

    # ---- receive video ----
    while True:
        try:
            pkt, _ = sock.recvfrom(65535)
        except (BlockingIOError, OSError):
            break
        if len(pkt) < 17 or pkt[0] != T_VIDEO or pkt[1:9] != SID:
            continue
        fid, idx, cnt = struct.unpack(">IHH", pkt[9:17])
        if next_id is not None and fid < next_id and next_id - fid < 1000:
            continue  # late packet for a frame we already skipped
        f =frames.setdefault(fid, [cnt, {}])
        f[1][idx] = pkt[17:]
        if len(f[1]) != cnt:
            continue
        data = b"".join(f[1][i] for i in range(cnt))
        # drop older incomplete frames -> packet loss -> need keyframe
        for old in [k for k in frames if k <= fid]:
            if old != fid and (next_id is None or old >= next_id):
                waiting_key = True
            del frames[old]
        if next_id is not None and fid > next_id:
            waiting_key = True
        next_id = fid + 1
        if waiting_key:
            if not is_keyframe(data):
                continue
            waiting_key = False
        try:
            out = dec.decode(data)
        except RuntimeError:
            waiting_key = True
            continue
        if out:
            w, h, rgb = out
            surface = pygame.image.frombuffer(rgb, (w, h), "RGB")
            if frame_size != (w, h):
                frame_size = (w, h)
                scale = min(info.current_w * 0.85 / w, info.current_h * 0.85 / h, 1)
                screen = pygame.display.set_mode((int(w * scale), int(h * scale)), pygame.RESIZABLE)
            fps_count += 1
    if len(frames) > 60:
        frames.clear()
        waiting_key = True

    # ---- input ----
    win_w, win_h = screen.get_size()
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            running = False
        elif e.type == pygame.MOUSEMOTION:
            send(T_INPUT, b"\x01" + struct.pack(">ff", e.pos[0] / win_w, e.pos[1] / win_h))
        elif e.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP) and e.button in BUTTONS:
            send(T_INPUT, b"\x01" + struct.pack(">ff", e.pos[0] / win_w, e.pos[1] / win_h))
            send(T_INPUT, bytes([2, BUTTONS[e.button], 1 if e.type == pygame.MOUSEBUTTONDOWN else 0]))
        elif e.type == pygame.MOUSEWHEEL:
            send(T_INPUT, b"\x03" + struct.pack(">h", -e.y))
        elif e.type == pygame.TEXTINPUT:
            send(T_INPUT, b"\x04" + e.text.encode())
        elif e.type in (pygame.KEYDOWN, pygame.KEYUP):
            down = 1 if e.type == pygame.KEYDOWN else 0
            if e.key in SPECIAL:
                send(T_INPUT, bytes([5, SPECIAL[e.key], down]))
            elif down and (e.mod & (pygame.KMOD_CTRL | pygame.KMOD_ALT)) and 32 < e.key < 127:
                send(T_INPUT, b"\x06" + chr(e.key).encode())  # Ctrl+C, Alt+F4 etc.

    # ---- draw ----
    if surface:
        if surface.get_size() != (win_w, win_h):
            screen.blit(pygame.transform.scale(surface, (win_w, win_h)), (0, 0))
        else:
            screen.blit(surface, (0, 0))
        pygame.display.flip()
    if now - fps_t >= 1:
        status = f"{fps_count} fps" if surface else "waiting for host…"
        pygame.display.set_caption(f"Remote {SESSION} — {status}")
        fps_count, fps_t = 0, now
    clock.tick(240)

pygame.quit()
