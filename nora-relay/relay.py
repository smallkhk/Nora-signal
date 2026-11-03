#!/usr/bin/env python3
"""NoraDesk relay — runs on your VPS. No extra packages needed.

  UDP 21900  : host <-> PC viewer (and host -> phone)
  TCP 21901  : phone/browser viewer (web page + WebSocket)

Run:            python3 relay.py
With HTTPS:     python3 relay.py --cert /etc/letsencrypt/live/DOMAIN/fullchain.pem \
                                 --key  /etc/letsencrypt/live/DOMAIN/privkey.pem
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import ssl
import struct
import time

T_HELLO_HOST, T_HELLO_VIEW, T_VIDEO, T_INPUT, T_KEYREQ, T_PING = 1, 2, 3, 4, 5, 6
TIMEOUT = 30
WS_MAX_BUFFER = 768 * 1024  # if a phone falls behind this much, skip to next keyframe
HERE = os.path.dirname(os.path.abspath(__file__))


def is_keyframe(data):
    i = data.find(b"\x00\x00\x01")
    while i != -1 and i + 3 < len(data):
        if data[i + 3] & 0x1F in (5, 7):
            return True
        i = data.find(b"\x00\x00\x01", i + 3)
    return False


class Session:
    def __init__(self):
        self.host = None          # (addr, t)
        self.viewer = None        # UDP viewer (addr, t)
        self.ws = set()           # web clients
        self.frames = {}          # fid -> [cnt, {idx: bytes}]  (reassembly for web)
        self.next_id = None
        self.last_keyreq = 0.0


sessions = {}
udp = None  # transport


def sess(sid):
    s = sessions.get(sid)
    if s is None:
        s = sessions[sid] = Session()
    return s


def host_alive(s):
    return s.host and time.time() - s.host[1] < TIMEOUT


def send_to_host(s, sid, t, token, payload=b""):
    if host_alive(s):
        udp.sendto(bytes([t]) + sid + token + payload, s.host[0])


def request_key(s, sid):
    now = time.time()
    if now - s.last_keyreq < 0.3 or not s.ws:
        return
    s.last_keyreq = now
    c = next(iter(s.ws))
    send_to_host(s, sid, T_KEYREQ, c.token)


# ---------------- UDP side ----------------

class Udp(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        global udp
        udp = transport

    def datagram_received(self, data, addr):
        if len(data) < 9:
            return
        now = time.time()
        t, sid = data[0], data[1:9]
        s = sess(sid)
        if t in (T_HELLO_HOST, T_VIDEO):
            if t == T_HELLO_HOST and (not s.host or s.host[0] != addr):
                print(f"[relay] host {addr} session {sid.decode(errors='ignore').strip()}", flush=True)
            s.host = (addr, now)
            if t == T_VIDEO:
                if s.viewer and now - s.viewer[1] < TIMEOUT:
                    udp.sendto(data, s.viewer[0])
                if s.ws and len(data) >= 17:
                    self.to_web(s, sid, data)
        else:
            if not s.viewer or s.viewer[0] != addr:
                print(f"[relay] viewer {addr} session {sid.decode(errors='ignore').strip()}", flush=True)
            s.viewer = (addr, now)
            if t != T_HELLO_VIEW and host_alive(s):
                udp.sendto(data, s.host[0])

    def to_web(self, s, sid, pkt):
        fid, idx, cnt = struct.unpack(">IHH", pkt[9:17])
        if s.next_id is not None and fid < s.next_id and s.next_id - fid < 1000:
            return
        f = s.frames.setdefault(fid, [cnt, {}])
        f[1][idx] = pkt[17:]
        if len(f[1]) != cnt:
            if len(s.frames) > 60:
                s.frames.clear()
            return
        data = b"".join(f[1][i] for i in range(cnt))
        lost = s.next_id is not None and fid > s.next_id
        for old in [k for k in s.frames if k <= fid]:
            del s.frames[old]
        s.next_id = fid + 1
        key = is_keyframe(data)
        if lost:
            for c in s.ws:
                c.wait_key = True
        msg = bytes([1 if key else 0]) + data
        need_key = False
        for c in list(s.ws):
            if c.wait_key and not key:
                need_key = True
                continue
            if c.writer.transport.get_write_buffer_size() > WS_MAX_BUFFER:
                c.wait_key = True
                need_key = True
                continue
            c.wait_key = False
            c.send(msg)
        if need_key:
            request_key(s, sid)


# ---------------- WebSocket side ----------------

class WsClient:
    def __init__(self, writer):
        self.writer = writer
        self.sid = None
        self.token = None
        self.wait_key = True

    def send(self, payload, opcode=2):
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
        try:
            self.writer.write(head + payload)
        except Exception:
            pass


async def read_frame(reader):
    b1, b2 = await reader.readexactly(2)
    op = b1 & 0x0F
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    if n > 1 << 20:
        raise ValueError("frame too big")
    mask = await reader.readexactly(4) if b2 & 0x80 else None
    data = await reader.readexactly(n)
    if mask:
        m = (mask * (n // 4 + 1))[:n]
        data = (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big") if n else b""
    return op, data


async def handle_http(reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    except Exception:
        writer.close()
        return
    lines = head.decode(errors="ignore").split("\r\n")
    path = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"
    hdr = {}
    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            hdr[k.strip().lower()] = v.strip()

    if path.startswith("/ws") and hdr.get("upgrade", "").lower() == "websocket":
        accept = base64.b64encode(hashlib.sha1((hdr.get("sec-websocket-key", "") +
                 "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await ws_loop(reader, writer)
        return

    if path in ("/", "/index.html"):
        try:
            body = open(os.path.join(HERE, "web.html"), "rb").read()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                         b"Cache-Control: no-cache\r\nContent-Length: " + str(len(body)).encode() +
                         b"\r\nConnection: close\r\n\r\n" + body)
        except OSError:
            writer.write(b"HTTP/1.1 500 Error\r\nConnection: close\r\n\r\nweb.html missing")
    else:
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    try:
        await writer.drain()
    except Exception:
        pass
    writer.close()


async def ws_loop(reader, writer):
    c = WsClient(writer)
    s = None
    peer = writer.get_extra_info("peername")
    try:
        while True:
            op, data = await read_frame(reader)
            if op == 8:
                break
            if op == 9:
                c.send(data, 10)
                continue
            if op == 1 and c.sid is None:  # login
                j = json.loads(data.decode())
                session = str(j.get("session", ""))[:8]
                pw = str(j.get("password", ""))
                c.sid = session.encode().ljust(8)[:8]
                c.token = hashlib.sha256(pw.encode() + session.encode()).digest()[:8]
                s = sess(c.sid)
                s.ws.add(c)
                print(f"[relay] web viewer {peer} session {session}", flush=True)
                c.send(json.dumps({"host": bool(host_alive(s))}).encode(), 1)
                send_to_host(s, c.sid, T_KEYREQ, c.token)
                continue
            if op == 2 and s is not None and data and data[0] in (T_INPUT, T_KEYREQ, T_PING):
                send_to_host(s, c.sid, data[0], c.token, data[1:])
                if data[0] == T_PING:
                    c.send(json.dumps({"host": bool(host_alive(s))}).encode(), 1)
                    try:
                        await writer.drain()
                    except Exception:
                        break
    except Exception:
        pass
    finally:
        if s is not None:
            s.ws.discard(c)
        try:
            writer.close()
        except Exception:
            pass


async def cleanup():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for k in list(sessions):
            s = sessions[k]
            if s.host and now - s.host[1] > TIMEOUT:
                s.host = None
            if s.viewer and now - s.viewer[1] > TIMEOUT:
                s.viewer = None
            if not s.host and not s.viewer and not s.ws:
                del sessions[k]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp", type=int, default=21900)
    ap.add_argument("--web", type=int, default=21901)
    ap.add_argument("--cert")
    ap.add_argument("--key")
    a = ap.parse_args()

    loop = asyncio.get_running_loop()
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 * 1024 * 1024)
    sock.bind(("0.0.0.0", a.udp))
    await loop.create_datagram_endpoint(Udp, sock=sock)

    ctx = None
    if a.cert and a.key:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(a.cert, a.key)
    await asyncio.start_server(handle_http, "0.0.0.0", a.web, ssl=ctx)
    print(f"[relay] UDP {a.udp} | web {'https' if ctx else 'http'}://0.0.0.0:{a.web}", flush=True)
    await cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
