#!/usr/bin/env bash
# ============================================================
#  潮汐 Tide · 一键部署（在本地执行，不是服务器上）
#  用法：bash deploy.sh <user@host> <域名> [备用端口] [-i 私钥路径]
#  例：  bash deploy.sh root@117.72.196.74 "artsi.site www.artsi.site" 8080
#        bash deploy.sh root@117.72.196.74 "artsi.site www.artsi.site" 8080 -i ~/.ssh/id_ed25519
#
#  做的事：上传站点运行所需的 7 个文件 + 两份 nginx 模板 + 初始化脚本，
#         然后远程执行 setup-server.sh。源码（.py / README）不上传。
# ============================================================
set -euo pipefail

TARGET="${1:-}"
DOMAIN="${2:-}"
PORT="${3:-8080}"
shift 3 2>/dev/null || true

# 可选私钥：-i <path>
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
if [ "${1:-}" = "-i" ] && [ -n "${2:-}" ]; then
  # 必须用绝对路径：ssh -i 给相对路径会被静默跳过，表现为"密钥无效"
  KEY=$(cd "$(dirname "$2")" 2>/dev/null && pwd)/$(basename "$2")
  SSH_OPTS="$SSH_OPTS -i $KEY -o IdentitiesOnly=yes"
fi

if [ -z "$TARGET" ] || [ -z "$DOMAIN" ]; then
  echo "用法: bash deploy.sh <user@host> <域名> [备用端口] [-i 私钥路径]"
  echo "例:   bash deploy.sh root@117.72.196.74 \"artsi.site www.artsi.site\" 8080 -i ~/.ssh/tide"
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# ---- 1. 收集交付文件 ----
echo "==> 收集站点文件"
FILES="index.html styles.css app.js tide_data.json feed_data.json screen_pool.json favicon.svg"
for f in $FILES; do
  if [ ! -f "$SRC/$f" ]; then
    echo "!! 缺少 $f" >&2
    exit 1
  fi
  cp "$SRC/$f" "$STAGE/"
done
cp "$SRC/deploy/nginx-tide.conf"      "$STAGE/"
cp "$SRC/deploy/nginx-tide-http.conf" "$STAGE/"
cp "$SRC/deploy/setup-server.sh"      "$STAGE/"
chmod +x "$STAGE/setup-server.sh"

TOTAL=$(cat $STAGE/* | wc -c)
echo "    $(echo $FILES | wc -w) 个站点文件 + 3 个部署文件，共 $((TOTAL/1024)) KB"

# ---- 2. 上传 ----
echo "==> 上传到 ${TARGET}:/tmp/tide-deploy"
ssh $SSH_OPTS "$TARGET" "rm -rf /tmp/tide-deploy && mkdir -p /tmp/tide-deploy"
scp $SSH_OPTS -q "$STAGE"/* "${TARGET}:/tmp/tide-deploy/"

# ---- 3. 远程初始化 ----
echo "==> 远程初始化（安装 nginx / 落配置 / 重启）"
ssh $SSH_OPTS "$TARGET" "cd /tmp/tide-deploy && bash setup-server.sh \"${DOMAIN}\" ${PORT}"

# ---- 4. 上传到站点根目录 ----
echo "==> 同步到 /var/www/tide"
ssh $SSH_OPTS "$TARGET" "cd /tmp/tide-deploy && cp -f $FILES /var/www/tide/ && { chown -R www-data:www-data /var/www/tide 2>/dev/null || chown -R nginx:nginx /var/www/tide 2>/dev/null || true; }; chmod -R 755 /var/www/tide; ls -la /var/www/tide"

HOST_ONLY="${TARGET#*@}"
HTTPS_FLAG=""
ssh $SSH_OPTS "$TARGET" "test -f /etc/letsencrypt/live/$(echo "$DOMAIN" | awk '{print $1}')/fullchain.pem" && HTTPS_FLAG="https" || HTTPS_FLAG="http"

echo ""
echo "============================================================"
if [ "$HTTPS_FLAG" = "https" ]; then
  echo " 部署完成：https://$(echo "$DOMAIN" | awk '{print $1}')/"
else
  echo " 部署完成：http://${HOST_ONLY}:${PORT}/index.html"
  echo ""
  echo " 证书尚未签发，站点当前是 HTTP。DNS 生效后执行："
  echo "   ssh -i <私钥> ${TARGET} \\"
  echo "     \"certbot certonly --webroot -w /var/www/tide -d $(echo "$DOMAIN" | sed 's/ / -d /g') --agree-tos -m <邮箱>\""
  echo " 签完重跑本脚本即自动切 HTTPS。"
fi
echo ""
echo " 若浏览器打不开，按这个顺序查："
echo "   1) 云厂商控制台防火墙是否放行 80 / 443（最常见）"
echo "   2) 服务器上 curl -I http://127.0.0.1/index.html 是否为 200"
echo "   3) systemctl status nginx 与 tail -20 /var/log/nginx/tide.error.log"
echo "============================================================"
