#!/usr/bin/env bash
# ============================================================
#  潮汐 Tide · 一键部署（在本地执行，不是服务器上）
#  用法：bash deploy.sh <user@host> [端口] [-i 私钥路径]
#  例：  bash deploy.sh root@117.72.196.74 80
#        bash deploy.sh root@117.72.196.74 80 -i ~/.ssh/id_ed25519
#
#  做的事：只上传站点运行所需的 6 个文件 + nginx 配置 + 初始化脚本，
#         然后远程执行 setup-server.sh。源码（.py / README）不上传。
# ============================================================
set -euo pipefail

TARGET="${1:-}"
PORT="${2:-80}"
shift 2 2>/dev/null || true

# 可选私钥：-i <path>
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
if [ "${1:-}" = "-i" ] && [ -n "${2:-}" ]; then
  SSH_OPTS="$SSH_OPTS -i $2"
fi

if [ -z "$TARGET" ]; then
  echo "用法: bash deploy.sh <user@host> [端口] [-i 私钥路径]"
  echo "例:   bash deploy.sh root@117.72.196.74 80"
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# ---- 1. 收集交付文件 ----
echo "==> 收集站点文件"
FILES="index.html styles.css app.js tide_data.json feed_data.json screen_pool.json"
for f in $FILES; do
  if [ ! -f "$SRC/$f" ]; then
    echo "!! 缺少 $f" >&2
    exit 1
  fi
  cp "$SRC/$f" "$STAGE/"
done
cp "$SRC/deploy/nginx-tide.conf"   "$STAGE/"
cp "$SRC/deploy/setup-server.sh"   "$STAGE/"
chmod +x "$STAGE/setup-server.sh"

TOTAL=$(cat $STAGE/* | wc -c)
echo "    $(echo $FILES | wc -w) 个站点文件 + 2 个部署脚本，共 $((TOTAL/1024)) KB"

# ---- 2. 上传 ----
echo "==> 上传到 ${TARGET}:/tmp/tide-deploy"
ssh $SSH_OPTS "$TARGET" "rm -rf /tmp/tide-deploy && mkdir -p /tmp/tide-deploy"
scp $SSH_OPTS -q "$STAGE"/* "${TARGET}:/tmp/tide-deploy/"

# ---- 3. 远程初始化 ----
echo "==> 远程初始化（安装 nginx / 落配置 / 重启）"
ssh $SSH_OPTS "$TARGET" "cd /tmp/tide-deploy && bash setup-server.sh ${PORT}"

# ---- 4. 上传到站点根目录 ----
echo "==> 同步到 /var/www/tide"
ssh $SSH_OPTS "$TARGET" "cd /tmp/tide-deploy && cp -f $FILES /var/www/tide/ && chown -R nginx:nginx /var/www/tide 2>/dev/null || chown -R www-data:www-data /var/www/tide 2>/dev/null || true; chmod -R 755 /var/www/tide; ls -la /var/www/tide"

HOST_ONLY="${TARGET#*@}"
echo ""
echo "============================================================"
echo " 部署完成：http://${HOST_ONLY}:${PORT}/index.html"
echo ""
echo " 若浏览器打不开，按这个顺序查："
echo "   1) 云厂商控制台防火墙是否放行 ${PORT}/tcp（最常见）"
echo "   2) 服务器上 curl -I http://127.0.0.1:${PORT}/index.html 是否为 200"
echo "   3) systemctl status nginx 与 tail -20 /var/log/nginx/tide.error.log"
echo "============================================================"
