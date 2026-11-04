import socket, select, struct, threading, time

PORT = 1080

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def relay(a, b):
    while True:
        try:
            r, _, _ = select.select([a, b], [], [a, b], 60)
            if not r: break
            for s in r:
                o = b if s is a else a
                d = s.recv(32768)
                if not d: return
                o.sendall(d)
        except: break

def handle(conn):
    try:
        h = conn.recv(2)
        if len(h) < 2 or h[0] != 5: return
        conn.recv(h[1])
        conn.sendall(b"\x05\x00")
        req = conn.recv(4)
        if len(req) < 4 or req[1] != 1:
            conn.sendall(b"\x05\x07\x00\x01" + b"\x00"*6); return
        atyp = req[3]
        if   atyp == 1: host = socket.inet_ntoa(conn.recv(4))
        elif atyp == 3: host = conn.recv(conn.recv(1)[0]).decode()
        elif atyp == 4: host = socket.inet_ntop(socket.AF_INET6, conn.recv(16))
        else: conn.sendall(b"\x05\x08\x00\x01" + b"\x00"*6); return
        port = struct.unpack(">H", conn.recv(2))[0]
        try:
            tgt = socket.create_connection((host, port), timeout=15)
        except:
            conn.sendall(b"\x05\x05\x00\x01" + b"\x00"*6); return
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00"*6)
        tgt.settimeout(None); conn.settimeout(None)
        relay(conn, tgt)
        tgt.close()
    except: pass
    finally:
        try: conn.close()
        except: pass

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", PORT))
srv.listen(64)

print(f"SOCKS5 proxy running on {get_ip()}:{PORT}")
print(f"Phone: Wi-Fi -> Proxy -> Manual -> Host: {get_ip()}  Port: {PORT}")
print("Press Ctrl+C to stop.\n")

while True:
    try:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        print("Stopped.")
        break
