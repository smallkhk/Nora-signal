#!/bin/bash
# Fix LiteSpeed + Flask-SocketIO WebSocket on cPanel/CloudLinux
# Run as: bash ~/mon/fix.sh
set -euo pipefail

APP="$HOME/mon"
VENV="$HOME/virtualenv/mon/3.11"
GUN="$VENV/bin/gunicorn"
BIND="127.0.0.1:5050"
PID="$APP/gunicorn.pid"
WORKER="geventwebsocket.gunicorn.workers.GeventWebSocketWorker"

# ── 1. Backup ─────────────────────────────────────────────────────────────────
cp "$APP/.htaccess" "$APP/.htaccess.bak.$(date +%s)"
echo "✓ .htaccess backed up"

# ── 2. Patch passenger_wsgi.py ────────────────────────────────────────────────
# gevent monkey-patch must happen before any other imports.
# GeventWebSocketWorker does this for Gunicorn workers, but it won't hurt to
# have it here too (idempotent for future lswsgi use).
if ! grep -q 'monkey.patch_all' "$APP/passenger_wsgi.py"; then
  python3 - <<'PY'
import re, pathlib
p = pathlib.Path('/home/ecliaoia/mon/passenger_wsgi.py')
src = p.read_text()
patch = 'from gevent import monkey\nmonkey.patch_all()\n\n'
p.write_text(patch + src)
print('✓ gevent monkeypatch prepended to passenger_wsgi.py')
PY
else
  echo "✓ gevent monkeypatch already in passenger_wsgi.py"
fi

# ── 3. Kill old Gunicorn ──────────────────────────────────────────────────────
if pkill -f "gunicorn.*passenger_wsgi" 2>/dev/null; then
  echo "✓ old Gunicorn killed"
  sleep 1
else
  echo "✓ no running Gunicorn found"
fi

# ── 4. Start Gunicorn as daemon ───────────────────────────────────────────────
cd "$APP"
"$GUN" \
  --worker-class "$WORKER" \
  --workers 1 \
  --bind "$BIND" \
  --timeout 120 \
  --access-logfile "$APP/gunicorn-access.log" \
  --error-logfile  "$APP/gunicorn-error.log" \
  --daemon \
  --pid "$PID" \
  passenger_wsgi:application

sleep 2

if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "✓ Gunicorn running  pid=$(cat "$PID")"
else
  echo "✗ Gunicorn failed to start — last error log:"
  tail -20 "$APP/gunicorn-error.log"
  exit 1
fi

# ── 5. Smoke-test Gunicorn locally ────────────────────────────────────────────
SHAKE=$(curl -sf --max-time 5 "http://$BIND/socket.io/?EIO=4&transport=polling" || true)
if [[ "$SHAKE" == 0\{* ]]; then
  echo "✓ local polling OK: ${SHAKE:0:80}"
else
  echo "✗ local polling failed: $SHAKE"
  echo "--- last 30 lines of error log ---"
  tail -30 "$APP/gunicorn-error.log"
  echo "----------------------------------"
fi

# ── 6. Write .htaccess ────────────────────────────────────────────────────────
cat > "$APP/.htaccess" << 'HTACCESS'
# CloudLinux Python Selector (serves non-socket.io routes via lswsgi)
PassengerEnabled On
PassengerAppRoot "/home/ecliaoia/mon"
PassengerBaseURI "/"
PassengerPython "/home/ecliaoia/virtualenv/mon/3.11/bin/python"

RewriteEngine On

# ── Socket.IO → Gunicorn ─────────────────────────────────────────────────────
# WebSocket upgrade: LiteSpeed only tunnels WebSocket when the backend URI
# uses the ws:// scheme. Using http:// here breaks WebSocket — it is the
# single most common mistake with LiteSpeed WebSocket proxying.
RewriteCond %{HTTP:Upgrade} =websocket [NC]
RewriteRule ^/?socket\.io/(.*)$ ws://127.0.0.1:5050/socket.io/$1 [P,L]

# HTTP long-polling (also goes to Gunicorn so sessions are shared)
RewriteRule ^/?socket\.io/(.*)$ http://127.0.0.1:5050/socket.io/$1 [P,L]

# Rewrite Location headers in responses so 3xx replies don't expose :5050
ProxyPassReverse /socket.io/ http://127.0.0.1:5050/socket.io/
HTACCESS

echo "✓ .htaccess written"

# ── 7. Cron watchdog (restart Gunicorn if it dies) ───────────────────────────
CRON_LINE="* * * * * pgrep -qf 'gunicorn.*passenger_wsgi' || (cd $APP && $GUN --worker-class $WORKER --workers 1 --bind $BIND --timeout 120 --access-logfile $APP/gunicorn-access.log --error-logfile $APP/gunicorn-error.log --daemon --pid $PID passenger_wsgi:application)"
(crontab -l 2>/dev/null | grep -v 'gunicorn.*passenger_wsgi'; echo "$CRON_LINE") | crontab -
echo "✓ cron watchdog installed (checks every minute)"

# ── 8. Test public WebSocket upgrade ─────────────────────────────────────────
echo ""
echo "=== Public WebSocket upgrade test ==="
curl --http1.1 -i --max-time 10 \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: SGVsbG9XU29ja2V0S2V5MTIzNA==" \
  "https://mon.eclipselivecam.online/socket.io/?EIO=4&transport=websocket" \
  2>&1 | head -25

echo ""
echo "=== Summary ==="
echo "  Gunicorn pid : $(cat "$PID")"
echo "  Bind         : $BIND"
echo "  Worker       : $WORKER"
echo ""
echo "Expected WebSocket result: HTTP/1.1 101 Switching Protocols"
echo "Expected polling result : 0{\"sid\":...}"
echo ""
echo "hub.html should connect to: io(\"https://mon.eclipselivecam.online\")"
echo "Serve hub.html from: $APP/public/hub.html  (LiteSpeed static)"
echo "  OR add to relay.py:  @app.route('/') -> send_from_directory(..., 'hub.html')"
