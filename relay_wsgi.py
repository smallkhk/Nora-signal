"""
LSAPI binary protocol server for LiteSpeed/CloudLinux.

Request packet (from LiteSpeed):
  [0-1]  'LS' magic
  [2]    type = 0x01 (request)
  [3]    flags
  [4-7]  total packet length (LE, includes this 8-byte header)
  [8-11] env_sz   -- bytes in CGI env block
  [12-15] spe_sz  -- bytes in special-env block (HTTP_* vars)
  [16-19] http_sz -- bytes of raw HTTP request (line + headers + body)
  [20-23] req_body_sz  -- appears to overlap / be 0 for GET
  [24 .. 24+env_sz)  -- CGI env vars in FastCGI name-value format
  [.. +spe_sz)       -- HTTP_* vars same format
  [.. +http_sz)      -- raw HTTP request bytes

FastCGI name-value format (used for env sections):
  namelen: 1 byte if < 128, else 4 bytes big-endian with high bit set
  vallen:  same
  name bytes (no null)
  value bytes (no null)

Response (sent back on the connection):
  Plain CGI text: "Status: 200 OK\r\nHeaders...\r\n\r\nbody"
  (no binary framing — LiteSpeed reads until connection close)
"""

import sys
import os
import struct
import socket as S
import io
import threading
import traceback
import time

sys.path.insert(0, '/home/ecliaoia/virtualenv/mon/3.11/lib/python3.11/site-packages')
sys.path.insert(0, '/home/ecliaoia/mon')

LOG = '/home/ecliaoia/mon/lsapi.log'
LSAPI_MAGIC = b'LS'
REQ_TYPE = 0x01


def log(msg):
    try:
        with open(LOG, 'a') as f:
            f.write(f"[{time.time():.3f}] {msg}\n")
    except Exception:
        pass


def read_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise EOFError(f"closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def parse_fastcgi_params(data):
    """Parse FastCGI name-value pairs (used by LSAPI for env blocks)."""
    env = {}
    i = 0
    while i < len(data):
        if i >= len(data):
            break
        # name length
        b = data[i]
        if b & 0x80:
            if i + 4 > len(data):
                break
            name_len = struct.unpack_from('>I', data, i)[0] & 0x7FFFFFFF
            i += 4
        else:
            name_len = b
            i += 1
        # value length
        if i >= len(data):
            break
        b = data[i]
        if b & 0x80:
            if i + 4 > len(data):
                break
            val_len = struct.unpack_from('>I', data, i)[0] & 0x7FFFFFFF
            i += 4
        else:
            val_len = b
            i += 1
        if i + name_len + val_len > len(data):
            break
        name = data[i:i + name_len].decode('latin-1', errors='replace')
        i += name_len
        val = data[i:i + val_len].decode('latin-1', errors='replace')
        i += val_len
        if name:
            env[name] = val
    return env


def handle_connection(conn):
    try:
        while True:
            # ── read packet header (8 bytes) ──────────────────────────────
            try:
                hdr = read_exact(conn, 8)
            except EOFError:
                break

            if hdr[:2] != LSAPI_MAGIC:
                log(f"bad magic {hdr[:4].hex()}")
                break

            ptype = hdr[2]
            total = struct.unpack('<I', hdr[4:8])[0]
            body_n = total - 8

            log(f"pkt type={ptype} total={total} body_n={body_n}")

            if body_n < 0 or body_n > 8 * 1024 * 1024:
                log(f"bad body_n={body_n}")
                break

            body = read_exact(conn, body_n)

            if ptype != REQ_TYPE:
                log(f"unexpected pkt type={ptype}")
                continue

            # ── parse 4-field body header ─────────────────────────────────
            if len(body) < 16:
                log(f"body too short ({len(body)})")
                break

            env_sz, spe_sz, http_sz, f3 = struct.unpack_from('<IIII', body, 0)
            log(f"env_sz={env_sz} spe_sz={spe_sz} http_sz={http_sz} f3={f3}")

            # Log raw bytes to determine actual format
            log(f"body[0:16] hex: {body[0:16].hex()}")
            log(f"body[16:56] hex: {body[16:56].hex()}")

            off = 16

            # ── parse env sections ────────────────────────────────────────
            env_data = body[off:off + env_sz]
            log(f"env_data[:40] hex: {env_data[:40].hex()}")
            off += env_sz

            spe_data = body[off:off + spe_sz]
            off += spe_sz

            http_raw = body[off:off + http_sz]
            log(f"http_raw[:80] hex: {http_raw[:80].hex()!r}")
            off += http_sz

            # f3 bytes follow (might be empty for GET, or contain stdin)
            rest = body[off:]
            log(f"rest[:80] hex: {rest[:80].hex()!r}")

            # Parse with FastCGI name-value format
            cgi_env = parse_fastcgi_params(env_data)
            spe_env = parse_fastcgi_params(spe_data)

            log(f"cgi_env: {dict(list(cgi_env.items())[:8])}")
            log(f"spe_env keys: {list(spe_env.keys())[:8]}")
            log(f"http_raw[:80]: {http_raw[:80]!r}")

            # ── parse request line ────────────────────────────────────────
            # http_raw contains the raw HTTP request; if empty, derive from env
            if http_raw:
                first_line = http_raw.split(b'\r\n')[0].decode('latin-1', errors='replace')
            else:
                # fall back to CGI env vars
                method_env = cgi_env.get('REQUEST_METHOD', 'GET')
                uri_env = cgi_env.get('REQUEST_URI', '/')
                proto_env = cgi_env.get('SERVER_PROTOCOL', 'HTTP/1.1')
                first_line = f"{method_env} {uri_env} {proto_env}"

            parts = first_line.split(' ')
            method = parts[0] if parts else 'GET'
            path   = parts[1] if len(parts) > 1 else '/'
            proto  = parts[2].strip() if len(parts) > 2 else 'HTTP/1.1'

            if '?' in path:
                path_info, qs = path.split('?', 1)
            else:
                path_info, qs = path, ''

            # ── build WSGI environ ────────────────────────────────────────
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
            environ['wsgi.input']      = io.BytesIO(rest)
            environ['wsgi.errors']     = sys.stderr
            environ['wsgi.multithread']  = True
            environ['wsgi.multiprocess'] = False
            environ['wsgi.run_once']     = False

            for hk, wk in (('HTTP_CONTENT_TYPE', 'CONTENT_TYPE'),
                            ('HTTP_CONTENT_LENGTH', 'CONTENT_LENGTH')):
                if hk in environ:
                    environ.setdefault(wk, environ.pop(hk))
            environ.setdefault('CONTENT_LENGTH', str(len(rest)))
            environ.setdefault('CONTENT_TYPE', '')

            # ── call WSGI app ─────────────────────────────────────────────
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

            # ── send plain CGI response (no binary framing) ───────────────
            hdr_str = f"Status: {resp_status}\r\n"
            for k, v in resp_headers:
                hdr_str += f"{k}: {v}\r\n"
            hdr_str += "\r\n"

            conn.sendall(hdr_str.encode('latin-1', errors='replace') + resp_body)
            # Close after each response (no keep-alive — restart loop won't re-read)
            break

    except Exception:
        log(f"handle_connection error:\n{traceback.format_exc()}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── main ──────────────────────────────────────────────────────────────────────
log("=== relay_wsgi starting ===")
log(f"pid={os.getpid()}")

try:
    server = S.socket(fileno=0)
    server.setblocking(True)
    log("attached fd 0 as listening socket")

    while True:
        try:
            conn, addr = server.accept()
            log(f"accepted from {addr!r}")
            t = threading.Thread(target=handle_connection, args=(conn,), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except Exception:
            log(f"accept error:\n{traceback.format_exc()}")
            time.sleep(0.05)

except Exception:
    log(f"fatal:\n{traceback.format_exc()}")
