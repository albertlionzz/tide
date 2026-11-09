# -*- coding: utf-8 -*-
"""
潮汐 · 候选池 K 线全量抓取
==========================
「个股分析」要跟着「股票筛选」条件实时切换，就必须让**整个候选池**都带
K 线，而不是像以前那样只抓写死的 5 只。否则前端一改条件就无米下锅。

抓取内容（东财 kline，klt=101 日线，fqt=1 前复权，与通达信口径一致）：
    f51 日期 / f53 收盘 / f56 成交量(手) / f57 成交额(元)

输出：pool_kline.json
    { asOf, count, items: [ {code,name,track,price,pct,turnoverRate,kline:[{d,c,v,a}]} ] }

体积控制：39 只 × 60 根 × 4 字段 ≈ 80KB，压缩后约 12KB，可接受。
"""

import json
import os
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://push2.eastmoney.com"
HIS = "https://push2his.eastmoney.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

N = 60


def get_json(url, referer="https://quote.eastmoney.com/", retries=3):
    headers = {"User-Agent": UA, "Accept": "*/*", "Referer": referer}
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"拉取失败 {url[:80]} -> {last}")


def setcode_of(code):
    if code.startswith(("6", "9")):
        return "1"
    if code.startswith(("4", "8")):
        return "2"
    return "0"


def fetch_kline(code):
    url = (f"{HIS}/api/qt/stock/kline/get?secid={setcode_of(code)}.{code}"
           "&fields1=f1,f2,f3&fields2=f51,f53,f56,f57&klt=101&fqt=1"
           f"&end=20500101&lmt={N}")
    d = get_json(url).get("data")
    if not d or not d.get("klines"):
        raise RuntimeError("K 线为空")
    out = []
    for ln in d["klines"]:
        p = ln.split(",")
        out.append({
            "d": p[0].replace("-", ""),
            "c": round(float(p[1]), 2),
            "v": int(float(p[2] or 0)),          # 手
            "a": round(float(p[3] or 0) / 1e8, 3),  # 亿元
        })
    return out


def fetch_quotes(codes):
    out = {}
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        secids = ",".join(f"{setcode_of(c)}.{c}" for c in chunk)
        url = (f"{BASE}/api/qt/ulist.np/get?fltt=2&invt=2&secids={secids}"
               "&fields=f2,f3,f8,f12,f14")
        d = get_json(url)
        for r in (d.get("data") or {}).get("diff") or []:
            def num(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
            out[r["f12"]] = {"name": r["f14"], "price": num(r.get("f2")),
                             "pct": num(r.get("f3")), "turnoverRate": num(r.get("f8"))}
        time.sleep(0.3)
    return out


def main():
    with open(os.path.join(HERE, "screen_pool.json"), "r", encoding="utf-8") as fh:
        pool = json.load(fh)
    snap_path = os.path.join(HERE, "raw_snapshot.json")
    with open(snap_path, "r", encoding="utf-8") as fh:
        snap = json.load(fh)
    trade_date = snap["tradeDate"]

    codes = [r["code"] for r in pool]
    print(f"候选池 {len(codes)} 只，抓 {N} 根日线 ...")
    quotes = fetch_quotes(codes)
    print(f"  行情 {len(quotes)} 只")

    items, bad = [], []
    for i, r in enumerate(pool, 1):
        c = r["code"]
        try:
            kl = fetch_kline(c)
        except Exception as e:                      # noqa: BLE001
            print(f"  [!] {c} {r['name']} K 线失败：{e}")
            bad.append(c)
            continue
        if len(kl) != N:
            print(f"  [!] {c} {r['name']} 只有 {len(kl)} 根（停牌/次新），按实际保留")
        q = quotes.get(c, {})
        items.append({
            "code": c,
            "name": q.get("name") or r["name"],
            "track": r["track"],
            "price": q.get("price"),
            "pct": q.get("pct"),
            "turnoverRate": q.get("turnoverRate"),
            "kline": kl,
        })
        if i % 10 == 0:
            print(f"  ... {i}/{len(pool)}")
        time.sleep(0.22)

    out = {
        "asOf": trade_date,
        "count": len(items),
        "missing": bad,
        "note": "东方财富日线（前复权），a 为成交额（亿元），v 为成交量（手）。",
        "items": items,
    }
    p = os.path.join(HERE, "pool_kline.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(p)
    print(f"完成 {len(items)} 只 → pool_kline.json {size / 1024:.1f} KB")

    # 自检：末根 K 线日期必须等于交易日，否则前端会把过期序列当今日
    stale = [it["code"] for it in items if it["kline"][-1]["d"] != trade_date.replace("-", "")]
    if stale:
        print(f"  [!] {len(stale)} 只末根不是 {trade_date}：{stale[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
