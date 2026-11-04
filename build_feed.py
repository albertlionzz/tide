#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
潮汐 · 资讯合并器
=================
把 feed_cls.json（财联社电报）+ feed_xueqiu.json（雪球热帖）
合并为前端可一次 fetch 的 feed_data.json，并额外产出：

  - stockBuzz : 按股票聚合的股民观点（"长期跟踪"视角）
                把雪球帖子里 $名称(代码)$ 的引用聚合成每只票的观点数 + 代表帖
  - hotTopics : 从财联社标签统计出的当日高频话题

这样前端只要读一个文件，就能同时渲染「快讯流」和「股民跟踪」两个视图。
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main():
    cls = load("feed_cls.json")
    xq = load("feed_xueqiu.json")

    cls_items = (cls or {}).get("items", [])
    xq_items = (xq or {}).get("items", [])

    # ---- 按股票聚合股民观点 ----
    buzz = defaultdict(lambda: {"name": "", "code": "", "posts": [], "authors": set()})
    for p in xq_items:
        for s in p.get("symbols", []):
            key = s["code"]
            b = buzz[key]
            b["name"] = s["name"]
            b["code"] = s["code"]
            b["authors"].add(p["author"])
            b["posts"].append({
                "author": p["author"], "time": p["time"],
                "snippet": p["content"][:160], "url": p.get("url", ""),
            })
    stock_buzz = []
    for code, b in buzz.items():
        stock_buzz.append({
            "code": code, "name": b["name"],
            "postCount": len(b["posts"]),
            "authorCount": len(b["authors"]),
            "authors": sorted(b["authors"])[:6],
            "posts": b["posts"][:5],
            "market": "沪" if code.startswith("SH") else "深" if code.startswith("SZ") else "北",
        })
    stock_buzz.sort(key=lambda x: (-x["postCount"], -x["authorCount"]))

    # ---- 财联社标签热度 ----
    tag_count = defaultdict(int)
    for it in cls_items:
        for t in it.get("tags", []):
            tag_count[t] += 1
    hot_topics = [{"tag": k, "count": v}
                  for k, v in sorted(tag_count.items(), key=lambda x: -x[1])]

    # ---- 重要快讯（star>=1）单独拎出 ----
    important = [it for it in cls_items if it.get("star", 0) >= 1]

    out = {
        "meta": {
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sources": [
                {"id": "cls", "name": "财联社电报", "url": "https://www.cls.cn/telegraph",
                 "role": "快讯 · 消息面驱动 · 短线情绪", "count": len(cls_items)},
                {"id": "xueqiu", "name": "雪球", "url": "https://xueqiu.com/today",
                 "role": "股民深度跟踪 · 长期研究 · 个股观点", "count": len(xq_items)},
            ],
        },
        "cls": cls_items,
        "xueqiu": xq_items,
        "important": important[:12],
        "hotTopics": hot_topics[:12],
        "stockBuzz": stock_buzz[:20],
        "stats": {
            "clsCount": len(cls_items),
            "xueqiuCount": len(xq_items),
            "buzzStockCount": len(stock_buzz),
            "importantCount": len(important),
        },
    }

    p = os.path.join(HERE, "feed_data.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("写出 feed_data.json  %d 字节" % os.path.getsize(p))
    print("  财联社 %d 条 | 雪球 %d 条 | 重要 %d 条 | 被讨论个股 %d 只"
          % (len(cls_items), len(xq_items), len(important), len(stock_buzz)))
    if stock_buzz:
        print("  讨论度 Top5:",
              " ".join("%s(%d)" % (b["name"], b["postCount"]) for b in stock_buzz[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
