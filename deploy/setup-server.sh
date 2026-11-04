#!/usr/bin/env bash
# ============================================================
#  潮汐 Tide · 服务器一次性初始化
#  适用：Ubuntu / Debian / CentOS / Rocky / Alma / OpenCloudOS / TencentOS
#  用法（在服务器上以 root 执行）：
#      bash setup-server.sh [端口]        # 默认 8080
#  幂等：重复执行只更新配置，不会破坏已有站点
# ============================================================
set -euo pipefail

PORT="${1:-80}"
ROOT="/var/www/tide"
CONF_SRC="$(cd "$(dirname "$0")" && pwd)/nginx-tide.conf"
CONF_DST="/etc/nginx/conf.d/tide.conf"

echo "==> 潮汐部署：端口 ${PORT}，根目录 ${ROOT}"

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
mkdir -p "$ROOT"
echo "==> 站点目录就绪：${ROOT}"

# ---------- 4. 落配置 ----------
if [ ! -f "$CONF_SRC" ]; then
  echo "!! 找不到 nginx-tide.conf（应与本脚本同目录）" >&2
  exit 1
fi
sed -e "s#__PORT__#${PORT}#g" -e "s#__ROOT__#${ROOT}#g" "$CONF_SRC" > "$CONF_DST"
echo "==> 写入配置：${CONF_DST}"

# 【关键】Ubuntu/Debian 装有默认站点 sites-enabled/default，它也监听 80 端口，
# 会把请求抢走并返回 404（root 指向 /var/www/html）。必须禁用，否则 80 端口白配。
if [ -e /etc/nginx/sites-enabled/default ]; then
  mv /etc/nginx/sites-enabled/default "/etc/nginx/sites-enabled/default.disabled.$(date +%s)"
  echo "==> 已禁用默认站点 sites-enabled/default（它会抢占 ${PORT} 端口）"
fi

# SELinux（CentOS 系）放行站点目录，否则 nginx 读不到文件
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = "Enforcing" ]; then
  echo "==> SELinux 为 Enforcing，放行 ${ROOT}"
  chcon -R -t httpd_sys_content_t "$ROOT" 2>/dev/null || true
  setsebool -P httpd_can_network_connect 1 2>/dev/null || true
fi

# ---------- 5. 系统防火墙 ----------
# 注意：ufw 的规则**不判断当前是否 active**。ufw 未启用时加规则同样会持久化，
# 将来一旦启用立即生效；反过来若只在 active 时加，apt 安装过程重置 ufw 就会丢规则。
# （实测踩过：脚本报告"已放行 8080"，实际 ufw status 里只剩 22/tcp）
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null && firewall-cmd --reload >/dev/null
  echo "==> firewalld 已放行 ${PORT}/tcp"
elif command -v ufw >/dev/null 2>&1; then
  ufw allow "${PORT}/tcp" >/dev/null 2>&1 || true
  # 22 端口兜底，避免启用 ufw 时把自己锁在门外
  ufw allow 22/tcp >/dev/null 2>&1 || true
  if ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw reload >/dev/null 2>&1 || true
    echo "==> ufw 已放行 ${PORT}/tcp（当前 active）"
  else
    echo "==> ufw 规则已写入 ${PORT}/tcp（ufw 当前 inactive，启用后自动生效）"
  fi
else
  echo "==> 系统防火墙未识别，跳过（请确认云厂商安全组）"
fi

# ---------- 6. 语法检查 + 启动 ----------
nginx -t
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl restart nginx
else
  service nginx restart
fi
echo "==> nginx 已重启"

# ---------- 7. 自检 ----------
sleep 1
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/index.html" || echo "000")
echo "==> 本机自测 http://127.0.0.1:${PORT}/index.html -> HTTP ${CODE}"

echo ""
echo "============================================================"
echo " 初始化完成。接下来的两件事："
echo ""
echo " 1) 上传站点文件（在你自己的电脑上执行）："
echo "      bash deploy.sh root@<服务器IP> ${PORT}"
echo ""
echo " 2) 云厂商控制台 -> 轻量云主机 -> 防火墙 -> 添加规则："
echo "      TCP，端口 ${PORT}，来源 0.0.0.0/0"
echo "    （云端防火墙不放行，公网一定访问不到，这一步最容易被忘。"
echo "      多数厂商默认只开 22/80/443，高位端口必须手动加。）"
echo "============================================================"
if [ "$PORT" = "80" ] || [ "$PORT" = "443" ]; then
  echo ""
  echo " 使用 80 端口：域名若未备案，国内节点可能被拦截。"
  echo " 直接用 IP 访问通常不受影响；绑定域名前请先完成备案。"
fi
