"""
LSAPI binary protocol server for LiteSpeed/CloudLinux.

Request structure:
  [0-7]  Packet header: 'LS' + type(1B) + flags(1B) + total_len(4B LE)
  [8-11] envListSize (4B LE)   - basic CGI env vars
  [12-15] speEnvListSize (4B LE) - HTTP_* env vars
  [16-19] httpHdrLen (4B LE)   - raw HTTP request line + headers
  [20-23] reqBodyLen (4B LE)   - request body length
  [24 .. 24+envListSize)        - CGI env vars as name\0value\0 pairs
  [.. +speEnvListSize)          - HTTP_* env vars same format
  [.. +httpHdrLen)              - raw HTTP request line + headers
  [.. +reqBodyLen)              - request body

Response structure (sent back on the same connection):
  pkt(type=2, CGI-format headers string)
  pkt(type=3, body bytes)   [if any body]
  pkt(type=4, b'')          [end of response]
"""

import sys
import os
import struct
import socket as S
import io
import threading
import traceback
import time

# ── paths ──────────────────────────────────────────────────────────────────
sys.path.insert(0, '/home/ecliaoia/virtualenv/mon/3.11/lib/python3.11/site-packages')
sys.path.insert(0, '/home/ecliaoia/mon')

LOG = '/home/ecliaoia/mon/lsapi.log'
LSAPI_MAGIC = b'LS'
REQ_TYPE       = 0x01
RESP_HDR_TYPE  = 0x02
RESP_BODY_TYPE = 0x03
RESP_END_TYPE  = 0x04


# ── helpers ────────────────────────────────────────────────────────────────

def log(msg):
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{time.time():.3f}] {msg}\n")
    except Exception:
        pass


def read_exact(sock, n):
    """Read exactly n bytes from sock, blocking."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise EOFError(f"connection closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def make_pkt(ptype, data=b''):
    """Build an 8-byte LSAPI packet header + data."""
    total = 8 + len(data)
    return LSAPI_MAGIC + bytes([ptype, 0]) + struct.pack('<I', total) + data


def parse_kv_list(raw):
    """Parse null-terminated key\\0value\\0 pairs from raw bytes."""
    env = {}
    i = 0
    while i < len(raw):
        j = raw.find(b'\x00', i)
        if j < 0:
            break
        name = raw[i:j].decode('latin-1', errors='replace')
        i = j + 1
        j = raw.find(b'\x00', i)
        if j < 0:
            break
        val = raw[i:j].decode('latin-1', errors='replace')
        i = j + 1
        if name:
            env[name] = val
    return env


# ── request handler ────────────────────────────────────────────────────────

def handle_connection(conn):
    """Handle one LSAPI connection (may carry multiple requests if keep-alive)."""
    try:
        while True:
            # ── read packet header (8 bytes) ───────────────────────────────
            try:
                hdr = read_exact(conn, 8)
            except EOFError:
                break   # client closed connection cleanly

            if hdr[:2] != LSAPI_MAGIC:
                log(f"bad magic {hdr[:4].hex()!r}; closing")
                break

            ptype  = hdr[2]
            flags  = hdr[3]
            total  = struct.unpack('<I', hdr[4:8])[0]
            body_n = total - 8

            log(f"recv pkt type={ptype} flags={flags} total={total}")

            if body_n < 0 or body_n > 4 * 1024 * 1024:
                log(f"bad body_n={body_n}; closing")
                break

            body = read_exact(conn, body_n)

            if ptype != REQ_TYPE:
                log(f"unexpected pkt type={ptype}; skipping")
                continue

            # ── parse body header (4 × int32) ──────────────────────────────
            if len(body) < 16:
                log(f"body too short ({len(body)} bytes) for header")
                break

            env_sz, spe_sz, http_sz, req_body_sz = struct.unpack_from('<IIII', body, 0)
            log(f"env_sz={env_sz} spe_sz={spe_sz} http_sz={http_sz} req_body_sz={req_body_sz}")

            off = 16
            needed = 16 + env_sz + spe_sz + http_sz + req_body_sz
            if needed > len(body):
                log(f"body underflow: need {needed}, have {len(body)}")
                break

            # ── parse env sections ─────────────────────────────────────────
            cgi_env = parse_kv_list(body[off:off + env_sz]);    off += env_sz
            spe_env = parse_kv_list(body[off:off + spe_sz]);    off += spe_sz
            http_raw = body[off:off + http_sz];                 off += http_sz
            req_body = body[off:off + req_body_sz]

            log(f"cgi_env keys={list(cgi_env.keys())[:6]}")
            log(f"spe_env keys={list(spe_env.keys())[:6]}")
            log(f"http_raw[:100]={http_raw[:100]!r}")

            # ── parse request line from raw HTTP section ───────────────────
            first_line = (http_raw.split(b'\r\n')[0]).decode('latin-1', errors='replace')
            parts = first_line.split(' ')
            method = parts[0] if parts else 'GET'
            path   = parts[1] if len(parts) > 1 else '/'
            proto  = parts[2].strip() if len(parts) > 2 else 'HTTP/1.1'

            if '?' in path:
                path_info, qs = path.split('?', 1)
            else:
                path_info, qs = path, ''

            # ── build WSGI environ ─────────────────────────────────────────
            environ = {}
            environ.update(cgi_env)
            environ.update(spe_env)

            environ['REQUEST_METHOD']  = method
            environ['PATH_INFO']       = path_info
            environ['QUERY_STRING']    = qs
            environ['SERVER_PROTOCOL'] = proto
            environ['wsgi.version']    = (1, 0)
            environ['wsgi.url_scheme'] = (
                'https' if environ.get('HTTPS', '').lower() == 'on' else 'http'
            )
            environ['wsgi.input']      = io.BytesIO(req_body)
            environ['wsgi.errors']     = sys.stderr
            environ['wsgi.multithread']  = True
            environ['wsgi.multiprocess'] = False
            environ['wsgi.run_once']     = False

            # Normalize Content-Type / Content-Length
            for hkey, wkey in (('HTTP_CONTENT_TYPE', 'CONTENT_TYPE'),
                                ('HTTP_CONTENT_LENGTH', 'CONTENT_LENGTH')):
                if hkey in environ:
                    environ.setdefault(wkey, environ.pop(hkey))
            environ.setdefault('CONTENT_LENGTH', str(req_body_sz))
            environ.setdefault('CONTENT_TYPE', '')

            # ── call WSGI app ──────────────────────────────────────────────
            from relay import app

            status_box  = []
            headers_box = []

            def start_response(status, headers, exc_info=None):
                if exc_info:
                    try:
                        raise exc_info[1].with_traceback(exc_info[2])
                    finally:
                        exc_info = None
                status_box[:] = [status]
                headers_box[:] = list(headers)

            body_chunks = []
            try:
                result = app(environ, start_response)
                try:
                    for chunk in result:
                        if chunk:
                            body_chunks.append(chunk)
                finally:
                    if hasattr(result, 'close'):
                        result.close()
            except Exception:
                tb = traceback.format_exc()
                log(f"WSGI exception:\n{tb}")
                if not status_box:
                    status_box[:] = ['500 Internal Server Error']
                    headers_box[:] = [('Content-Type', 'text/plain; charset=utf-8')]
                body_chunks = [tb.encode('utf-8')]

            resp_status  = status_box[0] if status_box else '500 Internal Server Error'
            resp_headers = headers_box
            resp_body    = b''.join(body_chunks)

            log(f"response: {resp_status!r}, body={len(resp_body)} bytes")

            # ── send LSAPI response ────────────────────────────────────────
            # Header packet: CGI-format "Status: …\r\nKey: Val\r\n\r\n"
            hdr_str = f"Status: {resp_status}\r\n"
            for k, v in resp_headers:
                hdr_str += f"{k}: {v}\r\n"
            hdr_str += "\r\n"
            hdr_bytes = hdr_str.encode('latin-1', errors='replace')

            conn.sendall(make_pkt(RESP_HDR_TYPE, hdr_bytes))
            if resp_body:
                conn.sendall(make_pkt(RESP_BODY_TYPE, resp_body))
            conn.sendall(make_pkt(RESP_END_TYPE, b''))

    except Exception:
        log(f"handle_connection error:\n{traceback.format_exc()}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── main ───────────────────────────────────────────────────────────────────

log("=== relay_wsgi starting ===")
log(f"pid={os.getpid()} env_keys={[k for k in os.environ if k.startswith('LSAPI')]}")

try:
    # fd 0 is the LSAPI Unix-domain listening socket
    server = S.socket(fileno=0)
    server.setblocking(True)
    log("attached to fd 0 as listening socket")

    while True:
        try:
            conn, addr = server.accept()
            log(f"accepted from {addr!r}")
            t = threading.Thread(target=handle_connection, args=(conn,), daemon=True)
            t.start()
        except KeyboardInterrupt:
            log("KeyboardInterrupt — exiting")
            break
        except Exception:
            log(f"accept error:\n{traceback.format_exc()}")
            time.sleep(0.05)

except Exception:
    log(f"fatal startup error:\n{traceback.format_exc()}")
