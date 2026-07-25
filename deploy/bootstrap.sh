#!/bin/bash
# Sentinel Mesh - EC2 bootstrap (Ubuntu 24.04)
# Runs as root via user-data on first boot. Safe to re-run by hand.
set -euxo pipefail

REPO_URL="${REPO_URL:-https://github.com/raeescassoojee/fusion.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/sentinel

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git nginx python3 python3-venv python3-pip \
  tesseract-ocr libgl1 libglib2.0-0 curl ca-certificates

# Node 20
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs

# ---------- application ----------
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin && git -C "$APP_DIR" reset --hard "origin/$BRANCH"
fi
cd "$APP_DIR"

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

npm --prefix services/chat install --omit=dev
npm --prefix services/feedback install --omit=dev

# WebSocket path must match the nginx /chat/ prefix
sed -i 's#\${protocol}//\${location.host}/ws#${protocol}//${location.host}/chat/ws#' \
  services/chat/client.html || true

mkdir -p services/operations/data services/chat/data services/feedback/data
id -u sentinel &>/dev/null || useradd --system --home "$APP_DIR" sentinel
chown -R sentinel:sentinel "$APP_DIR"

# ---------- environment ----------
# Region/bucket must match what provision_aws.py already created.
cat > /etc/sentinel.env <<EOF
AWS_REGION=eu-west-1
SENTINEL_EVIDENCE_BUCKET=sentinel-mesh-evidence-426421369712-eu-west-1
SENTINEL_PLATE_SALT=${SENTINEL_PLATE_SALT:-CHANGE-ME-LONG-RANDOM}
SENTINEL_AUTO_PUBLISH_AWS=1
SENTINEL_SYSTEM_KEY=${SENTINEL_SYSTEM_KEY:-demo-secret}
SENTINEL_CHAT_URL=http://127.0.0.1:8080
EOF
chmod 600 /etc/sentinel.env

# ---------- services ----------
cat > /etc/systemd/system/sentinel-ops.service <<'EOF'
[Unit]
Description=Sentinel operations API
After=network.target

[Service]
User=sentinel
WorkingDirectory=/opt/sentinel/services/operations
EnvironmentFile=/etc/sentinel.env
Environment=PYTHONPATH=/opt/sentinel/services/operations/src:/opt/sentinel/src
ExecStart=/opt/sentinel/.venv/bin/uvicorn sentinel_ops.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/sentinel-chat.service <<'EOF'
[Unit]
Description=Sentinel community chat
After=network.target

[Service]
User=sentinel
WorkingDirectory=/opt/sentinel/services/chat
EnvironmentFile=/etc/sentinel.env
Environment=PORT=8080
Environment=HOST=127.0.0.1
ExecStart=/usr/bin/node server.js
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/sentinel-feedback.service <<'EOF'
[Unit]
Description=Sentinel feedback service
After=network.target

[Service]
User=sentinel
WorkingDirectory=/opt/sentinel/services/feedback
EnvironmentFile=/etc/sentinel.env
Environment=PORT=8090
Environment=HOST=127.0.0.1
ExecStart=/usr/bin/node server.js
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# ---------- nginx ----------
cat > /etc/nginx/sites-available/sentinel <<'EOF'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 64M;

    # chat websocket - must come before the /chat/ prefix rule
    location /chat/ws {
        proxy_pass http://127.0.0.1:8080/ws;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }

    location /chat/ {
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
    }

    location /feedback/ {
        proxy_pass http://127.0.0.1:8090/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_read_timeout 300s;
    }
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/sentinel /etc/nginx/sites-enabled/sentinel
nginx -t

systemctl daemon-reload
systemctl enable --now sentinel-ops sentinel-chat sentinel-feedback
systemctl restart nginx

sleep 5
curl -sf http://127.0.0.1/health && echo "OPS OK"
curl -sf http://127.0.0.1/chat/health && echo "CHAT OK"
curl -sf http://127.0.0.1/feedback/health && echo "FEEDBACK OK"
echo "BOOTSTRAP COMPLETE"
