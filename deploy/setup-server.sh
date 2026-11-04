#!/usr/bin/env bash
# ============================================================
#  潮汐 Tide · 服务器一次性初始化
#  适用：Ubuntu / Debian / CentOS / Rocky / Alma / OpenCloudOS / TencentOS
#
#  用法（在服务器上以 root 执行）：
#      bash setup-server.sh <域名> [明文备用端口]
#  示例：
#      bash setup-server.sh "artsi.site www.artsi.site" 8080
#
#  脚本会自动判断阶段：
#    - 证书不存在 -> 落纯 HTTP 配置，让你能先跑 certbot
#    - 证书已存在 -> 落 HTTPS 配置（80 只做跳转 + 续期验证）
#  幂等：可重复执行，不会破坏已有站点
# ============================================================
set -euo pipefail

DOMAIN="${1:-}"
PORT="${2:-8080}"
ROOT="/var/www/tide"

if [ -z "$DOMAIN" ]; then
  echo "用法：bash setup-server.sh <域名> [明文备用端口]" >&2
  echo "示例：bash setup-server.sh \"example.com www.example.com\" 8080" >&2
  exit 1
fi

FIRST_DOMAIN=$(echo "$DOMAIN" | awk '{print $1}')
CERT_DIR="/etc/letsencrypt/live/${FIRST_DOMAIN}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ -f "${CERT_DIR}/fullchain.pem" ]; then
  CONF_SRC="${HERE}/nginx-tide.conf"
  STAGE="HTTPS"
else
  CONF_SRC="${HERE}/nginx-tide-http.conf"
  STAGE="HTTP（证书尚未签发）"
fi

echo "==> 潮汐部署：域名 ${DOMAIN}"
echo "==> 阶段：${STAGE}，根目录 ${ROOT}，备用端口 ${PORT}"

# ---------- 1. 识别发行版 ----------
if command -v apt-get >/dev/null 2>&1; then
  PKG=apt
elif command -v dnf >/dev/null 2>&1; then
  PKG=dnf
elif command -v yum >/dev/null 2>&1; then
  PKG=yum
else
  echo "!! 无法识别包管理器，请手动安装 nginx" >&2
  exit 1
fi
echo "==> 包管理器：${PKG}"

# ---------- 2. 安装 nginx ----------
if command -v nginx >/dev/null 2>&1; then
  echo "==> nginx 已安装：$(nginx -v 2>&1)"
else
  echo "==> 安装 nginx ..."
  case "$PKG" in
    apt) apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx ;;
    dnf) dnf install -y -q nginx ;;
    yum) yum install -y -q nginx ;;
  esac
fi

# ---------- 3. 建目录 ----------
mkdir -p "$ROOT" /etc/nginx/snippets
echo "==> 站点目录就绪：${ROOT}"

# ---------- 4. 公共安全头 ----------
# nginx 的 add_header 继承规则：location 里只要出现任意 add_header，
# server 层的 add_header 就全部失效。所以安全头必须逐 location include，
# 不能只写在 server 层（否则带 Cache-Control 的路径会丢掉安全头）。
cat > /etc/nginx/snippets/tide-headers.conf <<'EOF'
add_header Strict-Transport-Security "max-age=31536000" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
EOF
echo "==> 已写入 /etc/nginx/snippets/tide-headers.conf"

# ---------- 5. 落配置 ----------
if [ ! -f "$CONF_SRC" ]; then
  echo "!! 找不到 ${CONF_SRC}（应与本脚本同目录）" >&2
  exit 1
fi
sed -e "s#__DOMAIN__#${DOMAIN}#g" \
    -e "s#__ROOT__#${ROOT}#g" \
    -e "s#__CERT_DIR__#${CERT_DIR}#g" \
    -e "s#__PORT__#${PORT}#g" "$CONF_SRC" > /etc/nginx/conf.d/tide.conf
echo "==> 写入配置：/etc/nginx/conf.d/tide.conf"

# 【关键】Ubuntu/Debian 的 sites-enabled/default 也监听 80，
# 会抢走请求返回 404（root 指向 /var/www/html）。必须禁用，否则 80 端口白配。
if [ -e /etc/nginx/sites-enabled/default ]; then
  mv /etc/nginx/sites-enabled/default "/etc/nginx/sites-enabled/default.disabled.$(date +%s)"
  echo "==> 已禁用默认站点 sites-enabled/default（它会抢占 80 端口）"
fi

# SELinux（CentOS 系）放行站点目录，否则 nginx 读不到文件
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = "Enforcing" ]; then
  echo "==> SELinux 为 Enforcing，放行 ${ROOT}"
  chcon -R -t httpd_sys_content_t "$ROOT" 2>/dev/null || true
  setsebool -P httpd_can_network_connect 1 2>/dev/null || true
fi

# ---------- 6. 系统防火墙 ----------
# 注意：ufw 规则**不判断当前是否 active**。ufw 未启用时加规则同样会持久化，
# 将来一旦启用立即生效；反过来若只在 active 时加，apt 安装过程重置 ufw 就会丢规则。
# （实测踩过：脚本报告"已放行 8080"，实际 ufw status 里只剩 22/tcp）
PORTS="80 443 ${PORT}"
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  for p in $PORTS; do firewall-cmd --permanent --add-port="${p}/tcp" >/dev/null; done
  firewall-cmd --reload >/dev/null
  echo "==> firewalld 已放行：${PORTS}"
elif command -v ufw >/dev/null 2>&1; then
  for p in $PORTS; do ufw allow "${p}/tcp" >/dev/null 2>&1 || true; done
  # 22 端口兜底，避免启用 ufw 时把自己锁在门外
  ufw allow 22/tcp >/dev/null 2>&1 || true
  if ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw reload >/dev/null 2>&1 || true
    echo "==> ufw 已放行：${PORTS}（当前 active）"
  else
    echo "==> ufw 规则已写入：${PORTS}（ufw 当前 inactive，启用后自动生效）"
  fi
else
  echo "==> 系统防火墙未识别，跳过（请确认云厂商安全组）"
fi

# ---------- 7. 语法检查 + 启动 ----------
nginx -t
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl restart nginx
else
  service nginx restart
fi
echo "==> nginx 已重启"

# ---------- 8. 自检 ----------
sleep 1
if [ "$STAGE" = "HTTPS" ]; then
  CODE=$(curl -sk -o /dev/null -w "%{http_code}" --resolve "${FIRST_DOMAIN}:443:127.0.0.1" \
         "https://${FIRST_DOMAIN}/index.html" || echo "000")
  echo "==> 本机自测 https://${FIRST_DOMAIN}/index.html -> HTTP ${CODE}"
else
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: ${FIRST_DOMAIN}" \
         "http://127.0.0.1/index.html" || echo "000")
  echo "==> 本机自测 http://127.0.0.1/index.html -> HTTP ${CODE}"
fi

# ---------- 9. 后续步骤 ----------
echo ""
echo "============================================================"
if [ "$STAGE" = "HTTP（证书尚未签发）" ]; then
  echo " 证书还没签。DNS 解析生效后执行："
  echo ""
  echo "   DNS 检查："
  echo "     dig +short ${FIRST_DOMAIN}"
  echo ""
  echo "   申请证书（覆盖全部域名，注意引号）："
  echo "     certbot certonly --webroot -w ${ROOT} \\"
  for d in $DOMAIN; do echo "       -d ${d} \\"; done
  echo "       --agree-tos -m <你的邮箱> --preferred-challenges http"
  echo ""
  echo "   签完重跑本脚本，会自动切到 HTTPS："
  echo "     bash setup-server.sh \"${DOMAIN}\" ${PORT}"
else
  echo " 已启用 HTTPS。后续："
  echo ""
  echo " 1) 上传站点文件（在本地执行）："
  echo "      bash deploy.sh root@<服务器IP> ${PORT}"
  echo ""
  echo " 2) 确认云厂商控制台防火墙放行 80 / 443："
  echo "      TCP，来源 0.0.0.0/0"
  echo "    多数厂商默认只开 22/80/443，高位端口必须手动加。"
  echo ""
  echo " 3) 证书续期由 certbot 的 systemd timer 自动完成，验证："
  echo "      certbot renew --dry-run"
fi
echo "============================================================"
