#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
潮汐 · 研习社 —— 论坛与留言板后端

零第三方依赖：Python 标准库 http.server + sqlite3，内存占用约 20 MB。
由 nginx 反代 /api/ → 127.0.0.1:8091，只监听回环地址，不直接对外。

API
    GET    /api/health
    GET    /api/config                   公开开关状态（留言板是否开放）
    GET    /api/posts?type=note|message&page=1&size=20&q=&category=
    POST   /api/posts                    {type,title,body,category,tags,author}
    GET    /api/posts/<id>               含评论
    POST   /api/posts/<id>/like
    POST   /api/posts/<id>/comments      {body,author}
    GET    /api/stats
    DELETE /api/posts/<id>               需 X-Admin-Token

环境变量
    TIDE_ADMIN_TOKEN     删除口令；不设置则删除接口关闭（返回 403）
    TIDE_DB              SQLite 路径，默认 ./community.db
    TIDE_PORT            默认 8091
    TIDE_MESSAGE_BOARD   留言板开关，默认 "0"（关闭）。
                         关闭时列表不返回、写入拒绝、单帖详情 404，
                         前后端同时生效，改完重启服务即可恢复。
    TIDE_COMMENT_OPEN    文章评论开关，默认 "1"（开放）。
"""

import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = os.environ.get("TIDE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "community.db"))
PORT = int(os.environ.get("TIDE_PORT", "8091"))
ADMIN_TOKEN = os.environ.get("TIDE_ADMIN_TOKEN", "").strip()

# 公开互动开关。留言板是匿名白板，风险最高，默认关闭：
# 关闭后既不下发历史留言，也不接受新留言（前端同时隐藏入口）。
# 需要恢复时 TIDE_MESSAGE_BOARD=1 重启 tide-community 即可，历史数据一条不丢。
MESSAGE_BOARD_OPEN = os.environ.get("TIDE_MESSAGE_BOARD", "0").strip() == "1"
COMMENT_OPEN = os.environ.get("TIDE_COMMENT_OPEN", "1").strip() == "1"

CST = timezone(timedelta(hours=8))

MAX_BODY_BYTES = 64 * 1024
LIMITS = {
    "title": 120,
    "body": 6000,
    "comment": 1200,
    "author": 24,
    "tags": 6,
    "tag": 12,
}
CATEGORIES = ("盘面复盘", "个股研究", "方法论", "交易心理", "复盘教训", "其他")
POST_TYPES = ("note", "message")

# 频率限制：滑动窗口，每 IP 每小时最多 N 次写入
RATE_WINDOW = 3600
RATE_MAX_POSTS = 20
RATE_MAX_COMMENTS = 60
_rate = {}
_rate_lock = threading.Lock()


# ============================================================
# 数据库
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                type       TEXT NOT NULL,
                title      TEXT NOT NULL DEFAULT '',
                body       TEXT NOT NULL,
                category   TEXT NOT NULL DEFAULT '其他',
                tags       TEXT NOT NULL DEFAULT '',
                author     TEXT NOT NULL DEFAULT '匿名',
                likes      INTEGER NOT NULL DEFAULT 0,
                ip_hash    TEXT NOT NULL DEFAULT '',
                deleted    INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id    INTEGER NOT NULL,
                body       TEXT NOT NULL,
                author     TEXT NOT NULL DEFAULT '匿名',
                ip_hash    TEXT NOT NULL DEFAULT '',
                deleted    INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_posts_type_time ON posts(type, deleted, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id, deleted);
            """
        )


# ============================================================
# 工具
# ============================================================
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(s, limit, default=""):
    """去控制字符、折叠空白、截断。前端一律用 textContent 渲染，不做 HTML 处理。"""
    if s is None:
        return default
    s = _CTRL.sub("", str(s)).replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = re.sub(r"\n{4,}", "\n\n\n", s).strip()
    return s[:limit] if s else default


def now_iso():
    return datetime.now(CST).isoformat(timespec="seconds")


def client_ip(handler):
    """nginx 反代会把真实 IP 放在 X-Real-IP / X-Forwarded-For。"""
    for h in ("X-Real-IP", "X-Forwarded-For"):
        v = handler.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return handler.client_address[0]


def ip_hash(ip):
    import hashlib
    return hashlib.sha256(("tide::" + ip).encode()).hexdigest()[:16]


def rate_check(ip, bucket, limit):
    key = (ip, bucket)
    now = time.time()
    with _rate_lock:
        arr = [t for t in _rate.get(key, []) if now - t < RATE_WINDOW]
        if len(arr) >= limit:
            _rate[key] = arr
            return False, len(arr)
        arr.append(now)
        _rate[key] = arr
        # 顺手清理过期键，防止字典无限增长
        if len(_rate) > 5000:
            for k in [k for k, v in _rate.items() if not v or now - v[-1] > RATE_WINDOW]:
                _rate.pop(k, None)
        return True, len(arr)


def row_to_post(r, comment_count=0):
    tags = [t for t in (r["tags"] or "").split(",") if t]
    return {
        "id": r["id"],
        "type": r["type"],
        "title": r["title"],
        "body": r["body"],
        "category": r["category"],
        "tags": tags,
        "author": r["author"],
        "likes": r["likes"],
        "comments": comment_count,
        "createdAt": r["created_at"],
    }


# ============================================================
# 路由处理
# ============================================================
def api_list_posts(q):
    ptype = q.get("type", "note")
    if ptype not in POST_TYPES:
        ptype = "note"
    # 留言板关闭时不下发任何留言内容（历史数据仍在库里，仅不再对外）
    if ptype == "message" and not MESSAGE_BOARD_OPEN:
        return {"items": [], "total": 0, "page": 1, "size": 20, "pages": 1, "closed": True}
    page = max(1, int(q.get("page", "1") or 1))
    size = min(50, max(1, int(q.get("size", "20") or 20)))
    kw = clean(q.get("q", ""), 40)
    cat = clean(q.get("category", ""), 20)

    where = ["p.deleted = 0", "p.type = ?"]
    args = [ptype]
    if kw:
        where.append("(p.title LIKE ? OR p.body LIKE ? OR p.tags LIKE ?)")
        like = f"%{kw}%"
        args += [like, like, like]
    if cat and cat in CATEGORIES:
        where.append("p.category = ?")
        args.append(cat)
    wsql = " AND ".join(where)

    with db() as c:
        total = c.execute(f"SELECT COUNT(*) FROM posts p WHERE {wsql}", args).fetchone()[0]
        rows = c.execute(
            f"""SELECT p.*, (SELECT COUNT(*) FROM comments cm
                             WHERE cm.post_id = p.id AND cm.deleted = 0) AS cc
                FROM posts p WHERE {wsql}
                ORDER BY p.created_at DESC, p.id DESC
                LIMIT ? OFFSET ?""",
            args + [size, (page - 1) * size],
        ).fetchall()

    return {
        "items": [row_to_post(r, r["cc"]) for r in rows],
        "total": total,
        "page": page,
        "size": size,
        "pages": max(1, (total + size - 1) // size),
    }


def api_get_post(pid):
    with db() as c:
        r = c.execute("SELECT * FROM posts WHERE id = ? AND deleted = 0", (pid,)).fetchone()
        if not r:
            return None
        if r["type"] == "message" and not MESSAGE_BOARD_OPEN:
            return None                                    # 留言关闭后详情一律当不存在
        cs = c.execute(
            "SELECT * FROM comments WHERE post_id = ? AND deleted = 0 ORDER BY created_at ASC, id ASC",
            (pid,),
        ).fetchall()
    post = row_to_post(r, len(cs))
    post["commentList"] = [
        {"id": x["id"], "body": x["body"], "author": x["author"], "createdAt": x["created_at"]}
        for x in cs
    ]
    return post


def api_create_post(payload, ip):
    ptype = payload.get("type", "note")
    if ptype not in POST_TYPES:
        return 400, {"error": "type 必须是 note 或 message"}
    if ptype == "message" and not MESSAGE_BOARD_OPEN:
        return 403, {"error": "留言板已暂时关闭，暂时只开放经验总结"}

    body = clean(payload.get("body"), LIMITS["body"])
    if len(body) < 2:
        return 400, {"error": "内容太短，至少 2 个字"}

    title = clean(payload.get("title"), LIMITS["title"])
    if ptype == "note" and not title:
        return 400, {"error": "经验总结需要填标题"}

    cat = clean(payload.get("category"), 20, "其他")
    if cat not in CATEGORIES:
        cat = "其他"

    raw_tags = payload.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = re.split(r"[,，\s]+", raw_tags)
    tags = []
    for t in raw_tags:
        t = clean(t, LIMITS["tag"])
        if t and t not in tags:
            tags.append(t)
    tags = tags[: LIMITS["tags"]]

    author = clean(payload.get("author"), LIMITS["author"], "匿名") or "匿名"

    with db() as c:
        cur = c.execute(
            """INSERT INTO posts (type,title,body,category,tags,author,likes,ip_hash,created_at)
               VALUES (?,?,?,?,?,?,0,?,?)""",
            (ptype, title, body, cat, ",".join(tags), author, ip_hash(ip), now_iso()),
        )
        pid = cur.lastrowid
    return 200, {"ok": True, "id": pid}


def api_create_comment(pid, payload, ip):
    if not COMMENT_OPEN:
        return 403, {"error": "评论已暂时关闭"}
    body = clean(payload.get("body"), LIMITS["comment"])
    if len(body) < 1:
        return 400, {"error": "留言内容不能为空"}
    author = clean(payload.get("author"), LIMITS["author"], "匿名") or "匿名"
    with db() as c:
        if not c.execute("SELECT 1 FROM posts WHERE id = ? AND deleted = 0", (pid,)).fetchone():
            return 404, {"error": "帖子不存在"}
        c.execute(
            "INSERT INTO comments (post_id,body,author,ip_hash,created_at) VALUES (?,?,?,?,?)",
            (pid, body, author, ip_hash(ip), now_iso()),
        )
    return 200, {"ok": True}


def api_like(pid):
    with db() as c:
        cur = c.execute("UPDATE posts SET likes = likes + 1 WHERE id = ? AND deleted = 0", (pid,))
        if cur.rowcount == 0:
            return 404, {"error": "帖子不存在"}
        likes = c.execute("SELECT likes FROM posts WHERE id = ?", (pid,)).fetchone()[0]
    return 200, {"ok": True, "likes": likes}


def api_config():
    return {
        "messageBoard": MESSAGE_BOARD_OPEN,
        "comments": COMMENT_OPEN,
        "notice": "" if MESSAGE_BOARD_OPEN else "留言板已暂时关闭，目前只开放经验总结",
    }


def api_stats():
    with db() as c:
        notes = c.execute("SELECT COUNT(*) FROM posts WHERE type='note' AND deleted=0").fetchone()[0]
        msgs = c.execute("SELECT COUNT(*) FROM posts WHERE type='message' AND deleted=0").fetchone()[0]
        cms = c.execute("SELECT COUNT(*) FROM comments WHERE deleted=0").fetchone()[0]
        authors = c.execute(
            "SELECT COUNT(DISTINCT author) FROM posts WHERE deleted=0 AND author != '匿名'"
        ).fetchone()[0]
    return {"notes": notes, "messages": msgs, "comments": cms, "authors": authors}


# ============================================================
# HTTP
# ============================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "TideCommunity/1.0"

    def log_message(self, fmt, *a):                      # 精简日志，交给 systemd 收集
        # 管道被下游关闭时写 stderr 会抛 BrokenPipe，进而拖死整个服务。
        # 日志失败绝不能影响请求处理，一律吞掉。
        try:
            sys.stderr.write(f"{self.log_date_time_string()} {self.requestline} "
                             f"{a[1] if len(a) > 1 else ''}\n")
        except Exception:                                # noqa: BLE001
            pass

    # ---------- 响应 ----------
    def _cors_headers(self):
        """只对本机来源放行，方便本地用 python -m http.server 预览前端。
        生产环境同源部署，根本不发 Origin，不受影响。"""
        origin = self.headers.get("Origin", "")
        if origin.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
            self.send_header("Vary", "Origin")

    def _send(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, 411
        if n <= 0:
            return {}, 200
        if n > MAX_BODY_BYTES:
            return None, 413
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8")), 200
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, 400

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path.rstrip("/") or "/"

        if path == "/api/health":
            return self._send(200, {"ok": True, "service": "tide-community", "time": now_iso()})

        if path == "/api/stats":
            return self._send(200, api_stats())

        if path == "/api/config":
            return self._send(200, api_config())

        if path == "/api/posts":
            try:
                return self._send(200, api_list_posts(q))
            except (ValueError, sqlite3.Error) as e:
                return self._send(400, {"error": str(e)})

        m = re.fullmatch(r"/api/posts/(\d+)", path)
        if m:
            post = api_get_post(int(m.group(1)))
            if not post:
                return self._send(404, {"error": "帖子不存在"})
            return self._send(200, post)

        return self._send(404, {"error": "not found"})

    # ---------- POST ----------
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        payload, code = self._read_json()
        if payload is None:
            msg = {400: "请求体不是合法 JSON", 411: "缺少 Content-Length", 413: "请求体过大"}.get(code, "请求非法")
            return self._send(code, {"error": msg})

        ip = client_ip(self)

        if path == "/api/posts":
            ok, n = rate_check(ip, "post", RATE_MAX_POSTS)
            if not ok:
                return self._send(429, {"error": f"发布过于频繁（1 小时 {RATE_MAX_POSTS} 条上限），请稍后再试"})
            c, obj = api_create_post(payload, ip)
            return self._send(c, obj)

        m = re.fullmatch(r"/api/posts/(\d+)/like", path)
        if m:
            ok, _ = rate_check(ip, "like", 240)
            if not ok:
                return self._send(429, {"error": "操作过于频繁"})
            c, obj = api_like(int(m.group(1)))
            return self._send(c, obj)

        m = re.fullmatch(r"/api/posts/(\d+)/comments", path)
        if m:
            ok, _ = rate_check(ip, "comment", RATE_MAX_COMMENTS)
            if not ok:
                return self._send(429, {"error": "留言过于频繁，请稍后再试"})
            c, obj = api_create_comment(int(m.group(1)), payload, ip)
            return self._send(c, obj)

        return self._send(404, {"error": "not found"})

    # ---------- DELETE ----------
    def do_DELETE(self):
        u = urlparse(self.path)
        m = re.fullmatch(r"/api/posts/(\d+)", u.path.rstrip("/"))
        if not m:
            return self._send(404, {"error": "not found"})
        if not ADMIN_TOKEN:
            return self._send(403, {"error": "服务端未配置删除口令，删除接口已关闭"})
        if self.headers.get("X-Admin-Token", "") != ADMIN_TOKEN:
            return self._send(403, {"error": "口令不正确"})
        with db() as c:
            cur = c.execute("UPDATE posts SET deleted = 1 WHERE id = ?", (int(m.group(1)),))
            c.execute("UPDATE comments SET deleted = 1 WHERE post_id = ?", (int(m.group(1)),))
        if cur.rowcount == 0:
            return self._send(404, {"error": "帖子不存在"})
        return self._send(200, {"ok": True})

    # ---------- OPTIONS（同源部署其实用不到，留作本地调试）----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self._cors_headers()
        self.end_headers()


def main():
    init_db()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print(f"tide-community listening on 127.0.0.1:{PORT}  db={DB_PATH}  "
          f"admin={'on' if ADMIN_TOKEN else 'off'}  "
          f"messageBoard={'on' if MESSAGE_BOARD_OPEN else 'OFF'}  "
          f"comments={'on' if COMMENT_OPEN else 'off'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
