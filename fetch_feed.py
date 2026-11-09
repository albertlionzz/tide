#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
潮汐 · 资讯抓取器
=================
抓取两个信源：
  1. 财联社电报  (https://www.cls.cn/telegraph)  —— 快讯 / 短线消息面
  2. 雪球热帖    (https://xueqiu.com/today)      —— 股民长期跟踪 / 深度观点

技术要点（已实测）：
  - 两家站点均为纯 CSR（客户端渲染），curl 拿到的 HTML 无正文。
  - 雪球对 curl 有阿里云 WAF 拦截（TLS 指纹级），但对真实浏览器指纹放行。
  - 因此统一走 Edge 无头模式 --dump-dom 取渲染后 DOM。
  - 雪球必须用移动端 UA（桌面 UA 会触发安全策略拦截页）。

输出：
  feed_cls.json      财联社电报
  feed_xueqiu.json   雪球热帖
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import html as html_mod
from datetime import datetime

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "_cache")


def render(url, ua=DESKTOP_UA, budget=12000, profile=None):
    """用 Edge 无头模式渲染页面，返回渲染后的完整 DOM 文本。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = os.path.join(CACHE_DIR, "_render_%d.html" % int(time.time() * 1000))
    cmd = [
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-dev-shm-usage", "--hide-scrollbars",
        "--window-size=1440,5200",
        "--virtual-time-budget=%d" % budget,
        "--user-agent=%s" % ua,
    ]
    if profile:
        cmd.append("--user-data-dir=%s" % profile)
    cmd += ["--dump-dom", url]
    try:
        with open(tmp, "wb") as fh:
            p = subprocess.run(cmd, stdout=fh, stderr=subprocess.DEVNULL, timeout=90)
        with open(tmp, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    finally:
        # 渲染用的临时 HTML 直接删除；某些环境 trash 接口不可用，忽略失败
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def strip_tags(s):
    """去标签 + 还原实体 + 压缩空白。"""
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    s = s.replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- 财联社
# 财联社 telegraph 用原子化 class，无语义类名。
# 实测稳定分隔行：<div class="p-t-20 p-b-20 b-b-w-1 b-b-s-s b-c-e6e7ea">
# 条目内部结构：
#   时间戳  <span style="font-weight:bold;color:rgb(222,4,34)">22:07:36</span>
#   标题    <span style="font-weight:bold;">【标题】</span>
#   正文    <span>财联社10月13日电，...</span>
#   标签    <a href="/subject/1098">美股动态</a>
#   阅读/评论 <div class="f-l c-999">阅9322</div> / <a href="/detail/2503827">评论<span>(0)</span></a>
CLS_SPLIT = re.compile(r'(?=<div class="p-t-20 p-b-20 b-b-w-1)')


def parse_cls(doc):
    """从财联社 telegraph 渲染 DOM 中提取电报条目。"""
    items = []
    for blk in CLS_SPLIT.split(doc)[1:]:
        if len(blk) > 20000:
            continue
        body_html = blk
        # 时间戳（红色加粗）
        m = re.search(r'color:\s*rgb\(222,\s*4,\s*34\)[^>]*>(\d{1,2}:\d{2}(?::\d{2})?)<', body_html)
        if not m:
            m = re.search(r'>(\d{1,2}:\d{2}:\d{2})<', body_html)
        t = m.group(1) if m else ""
        # 正文段落（保留 span 内超链接的股票/公司名不易分离，这里取纯文本）
        text = strip_tags(body_html)
        if len(text) < 15:
            continue
        # 财联社按天插入的日期分隔条（「2026.10.21 星期三 …」）不是电报正文，剔除
        if "星期" in text and re.search(r"\d{4}\.\d{1,2}\.\d{1,2}", text):
            continue
        # 去掉时间戳前缀、阅/评论尾巴
        text = re.sub(r"^\s*\d{1,2}:\d{2}(?::\d{2})?\s*", "", text)
        text = re.sub(r"\s*阅\d+.*$", "", text)
        # 标题（【】内）。允许到 90 字，否则「美股盘前要闻一览」这类长标题
        # 会因为找不到闭合括号而退化成一堆无意义前缀。
        tm = re.search(r"[【\[]([^】\]]{4,90})[】\]]", text)
        title = tm.group(1) if tm else ""
        # 无【】标题时，去掉「财联社X月X日电，」前缀后取首句
        clean = re.sub(r"^财联社\d+月\d+日电[，,]\s*", "", text)
        # 「据报道」「卡塔尔官员表示」这类只是引语开头，不构成标题，
        # 直接用正文首句代替，否则页面上会出现一堆「据报道」。
        generic = (not title) or len(title) < 8 or re.fullmatch(
            r"[^，。；：]{0,10}(表示|称|获悉|报道|宣布)$", title)
        if generic:
            body_head = re.sub(r"^据报道[，,]\s*", "", clean)
            head = re.split(r"[，。；：]", body_head)[0]
            if len(head) >= 8:
                title = head[:46]
        if not title:
            title = clean[:46]
        # 正文去掉标题重复部分
        body = clean
        if title and title in body:
            body = body.replace("【%s】" % title, "", 1).strip()
        # 标签
        tags = re.findall(r'href="/subject/\d+"[^>]*>([^<]{2,12})</a>', body_html)
        # 阅读量
        rv = re.search(r"阅\s*(\d+)", body_html)
        read = int(rv.group(1)) if rv else 0
        # 评论数 / 链接 id
        cm = re.search(r'href="/detail/(\d+)"', body_html)
        cid = cm.group(1) if cm else ""
        cmt = re.search(r"评论<span[^>]*>\((\d+)\)</span>", body_html)
        comments = int(cmt.group(1)) if cmt else 0
        # 重要程度：标题为红色加粗 = 重要
        star = 2 if re.search(r'color:\s*rgb\(222,\s*4,\s*34\)[^>]*>【', body_html) else (
            1 if "【" in text else 0)
        items.append({
            "time": t, "title": title, "content": body[:500],
            "tags": tags[:3], "read": read, "comments": comments,
            "id": cid, "star": star, "source": "财联社",
            "url": "https://www.cls.cn/detail/%s" % cid if cid else
                   "https://www.cls.cn/telegraph",
        })
    # 去重
    seen, out = set(), []
    for it in items:
        k = (it["time"], it["content"][:30])
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out[:50]


# ---------------------------------------------------------------- 雪球
# https://xueqiu.com/today 是 Vue 版页面，结构稳定：
#   <div class="home__timeline__item">
#     <a href="/<uid>/<status_id>" class="a-placeholder"></a>
#     <h3>...</h3>
#     <p><a href="https://xueqiu.com/S/SH600418">$江淮汽车(SH600418)$</a> 正文...</p>
#     <div class="home__timeline__item__ft">
#       <div class="auchor">
#         <a class="user-name" href="/3765100047">卢克和贝塔</a>
#         <span class="timestamp"><a href="/3765100047/412091406">8小时前</a></span>
#       </div>
#     </div>
#   </div>
XQ_SPLIT = re.compile(r'(?=<div class="home__timeline__item">)')
# 正文里出现的股票标签 $xxx(SH600519)$
XQ_SYMBOL = re.compile(r"\$([^$()]{2,20})\((S[HZ]\d{6}|B[JS]\d{6})\)\$")


def parse_xueqiu(doc):
    """从雪球 today 页面渲染 DOM 中提取股民帖子。"""
    items = []
    for blk in XQ_SPLIT.split(doc)[1:]:
        # 正文 = 第一个 <p>...</p>
        pm = re.search(r"<p>([\s\S]*?)</p>", blk)
        if not pm:
            continue
        text = strip_tags(pm.group(1))
        if len(text) < 20:
            continue
        # 作者
        am = re.search(r'class="user-name"[^>]*>([^<]{2,24})<', blk)
        if not am:
            am = re.search(r'class="[^"]*user-name[^"]*"[^>]*>([^<]{2,24})<', blk)
        author = strip_tags(am.group(1)) if am else ""
        # 用户 ID + 帖子 ID
        uid, sid = "", ""
        idm = re.search(r'href="/(\d+)/(\d+)"', blk)
        if idm:
            uid, sid = idm.group(1), idm.group(2)
        # 时间
        tm = re.search(r'class="timestamp"[^>]*>[\s\S]*?<a[^>]*>([^<]{2,24})</a>', blk)
        if not tm:
            tm = re.search(r'class="timestamp"[^>]*>([^<]{2,24})<', blk)
        t = strip_tags(tm.group(1)) if tm else ""
        # 关联股票
        syms = []
        for name, code in XQ_SYMBOL.findall(pm.group(1)):
            syms.append({"name": name, "code": code})
        items.append({
            "author": author or "雪球用户",
            "uid": uid,
            "time": t,
            "symbols": syms[:4],
            "content": text[:600],
            "url": "https://xueqiu.com/%s/%s" % (uid, sid) if sid
                   else "https://xueqiu.com/today",
            "source": "雪球",
        })
    seen, out = set(), []
    for it in items:
        k = it["content"][:40]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out[:50]


def main():
    os.makedirs(HERE, exist_ok=True)
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    result = {"fetchedAt": now, "sources": {}}

    # --- 财联社 ---
    print("[1/2] 抓取财联社电报 ...")
    try:
        doc = render("https://www.cls.cn/telegraph", DESKTOP_UA, budget=12000)
        items = parse_cls(doc)
        print("      渲染 %d 字节 -> 解析 %d 条" % (len(doc), len(items)))
        result["sources"]["cls"] = {
            "name": "财联社电报", "url": "https://www.cls.cn/telegraph",
            "position": "快讯 | 消息面驱动 | 短线情绪",
            "count": len(items), "items": items,
        }
        with open(os.path.join(HERE, "feed_cls.json"), "w", encoding="utf-8") as fh:
            json.dump(result["sources"]["cls"], fh, ensure_ascii=False, indent=2)
    except Exception as e:
        print("      [x] 财联社失败:", e)

    # --- 雪球 ---
    print("[2/2] 抓取雪球热帖 ...")
    try:
        prof = os.path.join(CACHE_DIR, "_xq_profile")
        doc = render("https://xueqiu.com/today", MOBILE_UA, budget=15000, profile=prof)
        items = parse_xueqiu(doc)
        print("      渲染 %d 字节 -> 解析 %d 条" % (len(doc), len(items)))
        result["sources"]["xueqiu"] = {
            "name": "雪球热帖", "url": "https://xueqiu.com/today",
            "position": "股民深度跟踪 | 长期研究 | 个股观点",
            "count": len(items), "items": items,
        }
        with open(os.path.join(HERE, "feed_xueqiu.json"), "w", encoding="utf-8") as fh:
            json.dump(result["sources"]["xueqiu"], fh, ensure_ascii=False, indent=2)
    except Exception as e:
        print("      [x] 雪球失败:", e)

    with open(os.path.join(HERE, "feed_index.json"), "w", encoding="utf-8") as fh:
        json.dump({"fetchedAt": now, "sources": {
            k: {kk: vv for kk, vv in v.items() if kk != "items"}
            for k, v in result["sources"].items()}}, fh, ensure_ascii=False, indent=2)

    print("\n完成。输出: feed_cls.json / feed_xueqiu.json / feed_index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
