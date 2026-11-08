#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在现有 nginx 配置里插入 /api/ 反代，幂等且失败自动回滚。"""
import shutil
import subprocess
import sys

CONF = "/etc/nginx/conf.d/tide.conf"
BAK = CONF + ".bak-api"

BLOCK = """    # ---- 研习社 API（论坛 / 留言板）----
    # 只反代到回环地址，后端不直接暴露公网
    location /api/ {
        proxy_pass http://127.0.0.1:8091;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
        client_max_body_size 256k;
    }

"""

src = open(CONF, encoding="utf-8").read()
if "location /api/" in src:
    print("已存在 /api/ 反代，跳过插入")
else:
    shutil.copy2(CONF, BAK)

    # 443 主站块：插在「主页」注释之前
    anchor_443 = "    # ---- 主页 ----"
    if anchor_443 not in src:
        print("!!! 未找到 443 主页锚点，中止")
        sys.exit(1)
    src = src.replace(anchor_443, BLOCK + anchor_443, 1)

    # 8080 备用块：插在其 location / 之前（该块带 X-Content-Type-Options）
    anchor_8080 = """    location / {
        add_header X-Content-Type-Options "nosniff" always;"""
    if anchor_8080 in src:
        src = src.replace(anchor_8080, BLOCK + anchor_8080, 1)
        print("已为 443 与 8080 插入 /api/ 反代")
    else:
        print("已为 443 插入 /api/ 反代（8080 锚点未命中，跳过）")

    open(CONF, "w", encoding="utf-8").write(src)

# 语法检查，失败回滚
r = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
out = (r.stdout or "") + (r.stderr or "")
print(out.strip().splitlines()[-1] if out.strip() else "(nginx -t 无输出)")
if r.returncode != 0:
    print("!!! 配置有误，回滚")
    if BAK:
        shutil.copy2(BAK, CONF)
    subprocess.run(["nginx", "-t"])
    sys.exit(1)

subprocess.run(["systemctl", "reload", "nginx"], check=True)
print("nginx 已 reload")
