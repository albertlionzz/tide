#!/usr/bin/env bash
# ============================================================
#  潮汐 · 启动本地服务（必须走 HTTP，file:// 会被 CORS 拦截）
#  用法：bash serve.sh   然后浏览器打开 http://127.0.0.1:8768/
# ============================================================
cd "$(dirname "$0")"
PY="C:/Users/zhouy/.workbuddy/binaries/python/versions/3.13.12/python.exe"
PORT="${1:-8768}"
echo "启动 http://127.0.0.1:$PORT/index.html"
exec "$PY" -m http.server "$PORT" --bind 127.0.0.1
