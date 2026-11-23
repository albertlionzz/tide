# -*- coding: utf-8 -*-
"""
妖股量化 · 数据层 · 全市场日线抓取

数据源选择（实测，2026-11-20）：
    东财 push2his   5 年 + 成交额 + 换手率，字段最全。但**已进入 IP 级频控**：
                    3 线程跑一阵后 40/40 全失败，冷却数十分钟仍不通。不可作为主源。
    腾讯 ifzq       单次只给 640 根（约 2.5 年），且无成交额/换手率。只作兜底。
    网易 chddata    502 Bad Gateway，已失效。
    新浪 MarketData **1023 根（约 4.2 年，2022-08 起），0.2~0.4s/只，稳定**。 ← 主源

新浪缺的字段用以下方式补齐（口径已标注，避免误用）：
    成交额  ≈ 收盘价 × 成交量。误差来自当日均价与收盘价的偏离，量级 < 2%，
            对 log_amt / amt_ratio 这类相对量足够。
    换手率  = 成交量 ÷ 流通股本 × 100。流通股本由新浪列表的流通市值 ÷ 现价反推，
            拿的是**当前**股本，历史曾送转/增发的票会有偏差。此字段仅作辅助特征。

输出格式与东财一致（f51..f61 布局），下游 limits.annotate 无需区分来源：
    [日期, 开, 收, 高, 低, 量(股), 额(元), 振幅%, 涨跌幅%, 涨跌额, 换手%]

用法：
    python fetch_daily.py                 # 全量（续跑，已有文件跳过）
    python fetch_daily.py --limit 30      # 试跑
    python fetch_daily.py --source em     # 东财（等频控解除后用，字段最全）
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
UNIVERSE = os.path.join(DATA, "universe.json")
FAILED = os.path.join(DATA, "failed.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SINA = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=1023")
EM_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

_lock = threading.Lock()


def sina_rows(sym: str, retries: int = 3) -> list[list[str]] | None:
    url = SINA.format(sym=sym)
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
            arr = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("gbk", "ignore"))
            if not arr:
                return []
            rows, prev = [], None
            for r in arr:
                o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
                v = float(r["volume"])                     # 股
                amt = round(c * v, 2)                      # 估算成交额（元）
                if prev:
                    pct = round((c / prev - 1) * 100, 2)
                    chg = round(c - prev, 2)
                    amp = round((h - l) / prev * 100, 2)
                else:
                    pct = chg = amp = ""
                rows.append([r["day"], f"{o:.2f}", f"{c:.2f}", f"{h:.2f}", f"{l:.2f}",
                             f"{v:.0f}", f"{amt:.2f}", f"{amp}", f"{pct}", f"{chg}", ""])
                prev = c
            return rows
        except Exception:                                  # noqa: BLE001
            pass
        time.sleep(1.0 * (i + 1) + random.random())
    return None


def em_rows(secid: str, retries: int = 3) -> list[list[str]] | None:
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2={EM_FIELDS2}"
           "&klt=101&fqt=1&beg=20210101&end=20500101")
    for i in range(retries):
        for u in (url, url.replace("https://", "http://")):
            try:
                req = urllib.request.Request(u, headers={
                    "User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
                kl = (json.loads(urllib.request.urlopen(req, timeout=30).read())
                      .get("data") or {}).get("klines")
                if kl:
                    return [x.split(",") for x in kl]
                return []
            except Exception:                              # noqa: BLE001
                pass
        time.sleep(1.2 * (i + 1) + random.random())
    return None


def save(code: str, secid: str, name: str, rows: list[list[str]], src: str) -> None:
    path = os.path.join(RAW, f"{code}.json.gz")
    tmp = path + ".tmp"
    payload = {"code": code, "secid": secid, "name": name, "src": src,
               "count": len(rows), "rows": rows}
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--source", choices=["sina", "em"], default="sina")
    ap.add_argument("--force", action="store_true", help="覆盖已存在文件")
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()

    os.makedirs(RAW, exist_ok=True)
    with open(UNIVERSE, "r", encoding="utf-8") as fh:
        uni = json.load(fh)["items"]
    print(f"股票池 {len(uni)} 只，源={args.source}")

    if args.retry_failed and os.path.exists(FAILED):
        with open(FAILED, "r", encoding="utf-8") as fh:
            bad = set(json.load(fh))
        uni = [x for x in uni if x["code"] in bad]
        print(f"只重跑失败 {len(uni)} 只")

    todo = uni if args.force else [
        x for x in uni if not os.path.exists(os.path.join(RAW, f"{x['code']}.json.gz"))]
    print(f"待抓 {len(todo)} 只（已有 {len(uni) - len(todo)} 只）")
    if args.limit:
        todo = todo[:args.limit]

    stat = {"ok": 0, "nodata": 0, "bad": [], "done": 0}
    t0 = time.time()

    def work(x: dict) -> None:
        if args.source == "sina":
            sym = ("sh" if x["secid"].startswith("1.") else "sz") + x["code"]
            rows = sina_rows(sym)
        else:
            rows = em_rows(x["secid"])
        with _lock:
            stat["done"] += 1
            n = stat["done"]
            if rows:
                save(x["code"], x["secid"], x["name"], rows, args.source)
                stat["ok"] += 1
            elif rows is not None:
                stat["nodata"] += 1
            else:
                stat["bad"].append(x["code"])
        if n % 200 == 0:
            el = time.time() - t0
            rate = n / el if el else 0
            print(f"  {n}/{len(todo)}  ok={stat['ok']} nodata={stat['nodata']} "
                  f"fail={len(stat['bad'])}  {rate:.1f} 只/s  "
                  f"剩余 {(len(todo) - n) / rate / 60 if rate else 0:.1f} 分钟", flush=True)
        if args.delay:
            time.sleep(args.delay + random.random() * 0.06)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))

    if stat["bad"]:
        old = []
        if os.path.exists(FAILED):
            with open(FAILED, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        with open(FAILED, "w", encoding="utf-8") as fh:
            json.dump(sorted(set(old) | set(stat["bad"])), fh)
    print(f"完成：新增 {stat['ok']}，无数据 {stat['nodata']}，失败 {len(stat['bad'])}，"
          f"耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
