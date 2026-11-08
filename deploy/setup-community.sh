#!/bin/bash
# 潮汐 · 研习社后端一次性初始化（Ubuntu / systemd）
# 幂等：可重复执行，token 只在首次生成
set -e

APP=/opt/tide-community
PORT=8091

mkdir -p "$APP"

# ---------- 管理员删除口令 ----------
if [ ! -f "$APP/.admin_token" ]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(24))" > "$APP/.admin_token"
  chmod 600 "$APP/.admin_token"
  echo "==> 已生成新的删除口令"
else
  echo "==> 复用已有删除口令"
fi
TOKEN=$(cat "$APP/.admin_token")

# ---------- 权限 ----------
# 服务以 www-data 运行，数据库与工作目录都要可写
chown -R www-data:www-data "$APP" 2>/dev/null || true
chmod 755 "$APP"
chmod 644 "$APP/server.py" 2>/dev/null || true

# ---------- systemd ----------
cat > /etc/systemd/system/tide-community.service <<EOF
[Unit]
Description=Tide Community API (forum & message board)
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$APP
Environment=TIDE_DB=$APP/community.db
Environment=TIDE_PORT=$PORT
Environment=TIDE_ADMIN_TOKEN=$TOKEN
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $APP/server.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$APP

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tide-community >/dev/null 2>&1
systemctl restart tide-community

# ---------- 自检 ----------
for i in $(seq 1 12); do
  sleep 0.7
  if curl -sf -m 3 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "==> 服务已就绪"
    curl -s -m 3 "http://127.0.0.1:$PORT/api/health"
    echo
    echo "ADMIN_TOKEN=$TOKEN"
    exit 0
  fi
done

echo "!!! 服务启动失败，最近日志："
journalctl -u tide-community -n 25 --no-pager
exit 1
