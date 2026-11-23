# -*- coding: utf-8 -*-
"""
妖股量化 · 数据层 · 龙虎榜历史（3 年）

为什么先抓这个：
    1. datacenter-web 报表接口**不参与 clist 的频控**，实测 2023-06 至今均可回溯；
    2. 妖股必然上过龙虎榜 —— 由龙虎榜反推股票池，比 clist 全市场列表更贴合研究目标，
       而且绕开了 clist 的 RemoteDisconnected 封锁；
    3. 席位（机构/游资/北向）是妖股成色最关键的旁证，日线里看不到。

落盘：data/lhb/<YYYYMMDD>.json.gz，每日一文件；data/lhb_index.json 记已完成日期。

报表：RPT_DAILYBILLBOARD_DETAILSNEW（个股级，含买卖额、上榜原因、解读）
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "lhb")
INDEX = os.path.join(HERE, "data", "lhb_index.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def get(url: str, retries: int = 4) -> dict:
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as e:                        # noqa: BLE001
            last = e
            time.sleep(2 + i * 2)
    raise RuntimeError(f"失败: {last}")


def fetch_day(d: str) -> list[dict] | None:
    """d 形如 2023-06-16。返回当日龙虎榜个股列表；无数据返回 None。"""
    filt = urllib.parse.quote(f"(TRADE_DATE='{d}')")
    url = (f"{API}?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL"
           f"&filter={filt}&pageNumber=1&pageSize=500&source=WEB&client=WEB")
    j = get(url)
    res = j.get("result")
    if not res or not res.get("data"):
        return None
    out = []
    for r in res["data"]:
        out.append({
            "code": r.get("SECURITY_CODE"),
            "name": r.get("SECURITY_NAME_ABBR"),
            "market": r.get("MARKET"),
            "close": r.get("CLOSE_PRICE"),
            "pct": r.get("CHANGE_RATE"),
            "buy": r.get("BILLBOARD_BUY_AMT"),
            "sell": r.get("BILLBOARD_SELL_AMT"),
            "net": r.get("BILLBOARD_NET_AMT"),
            "deal": r.get("ACCUM_AMOUNT"),
            "reason": r.get("EXPLANATION"),
            "turn": r.get("TURNOVERRATE"),
        })
    return out


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:                 # 先排除周末，节假日靠接口返空识别
            yield d
        d += timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--delay", type=float, default=0.35)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    idx = {"done": [], "empty": [], "span": [args.start, args.end]}
    if os.path.exists(INDEX):
        with open(INDEX, "r", encoding="utf-8") as fh:
            idx = json.load(fh)
    done = set(idx.get("done", []))
    empty = set(idx.get("empty", []))

    days = [d for d in weekdays(date.fromisoformat(args.start),
                                date.fromisoformat(args.end))
            if d.isoformat() not in done and d.isoformat() not in empty]
    print(f"待抓 {len(days)} 个工作日（已完成 {len(done)}，无数据 {len(empty)}）")

    codes = {}
    for i, d in enumerate(days, 1):
        ds = d.isoformat()
        try:
            rows = fetch_day(ds)
        except Exception as e:                        # noqa: BLE001
            print(f"  [!] {ds} {type(e).__name__}，跳过明日再跑")
            continue
        if rows is None:
            empty.add(ds)
        else:
            path = os.path.join(OUT, f"{ds.replace('-', '')}.json.gz")
            tmp = path + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
            done.add(ds)
            for r in rows:
                if r["code"]:
                    codes[r["code"]] = r["name"]
        if i % 50 == 0:
            print(f"  {i}/{len(days)}  已完成 {len(done)} 天，股票池 {len(codes)} 只", flush=True)
        time.sleep(args.delay)

    idx["done"] = sorted(done)
    idx["empty"] = sorted(empty)
    idx["count"] = len(done)
    json.dump(idx, open(INDEX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 股票池：从所有已抓文件重新聚合（幂等）
    pool = {}
    for f in os.listdir(OUT):
        if not f.endswith(".json.gz"):
            continue
        with gzip.open(os.path.join(OUT, f), "rt", encoding="utf-8") as fh:
            for r in json.load(fh):
                if r.get("code"):
                    pool.setdefault(r["code"], {"code": r["code"], "name": r.get("name"),
                                                "market": r.get("market"), "times": 0})
                    pool[r["code"]]["times"] += 1
    items = sorted(pool.values(), key=lambda x: -x["times"])
    json.dump({"asOf": date.today().isoformat(), "count": len(items), "items": items},
              open(os.path.join(HERE, "data", "pool_from_lhb.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"完成：{len(done)} 个交易日，股票池 {len(items)} 只 -> data/pool_from_lhb.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
