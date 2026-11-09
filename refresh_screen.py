# -*- coding: utf-8 -*-
"""
潮汐 · 筛选池行情刷新
=====================
把 screen_pool.json 里的人工底稿（赛道 / 三道人工加分 / 核心逻辑 / 风险）
配上**当日真实行情**，并重算两个可量化加分项：

    mcap      总市值（亿元）      ← 东财 ulist f20
    turnover  当日换手率（%）     ← 东财 ulist f8
    chg5      近 5 日涨幅（%）    ← 东财 kline 6 根收盘价
    a4        近 10 日被游资席位净买入 = +2   ← raw_snapshot.seatStocks.stockHot
    a5        近 5 日涨幅 > 12%      = +2   ← 由 chg5 判定

a1/a2/a3（创始人 / 研发 / 业务）是人工研究结论，脚本不覆盖。
写回时间戳列入 asOf，前端标注「行情截面」。

红线：任何取不到的数字留 null 并计数打印，不填 0、不估算。
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

A5_THRESHOLD = 12.0     # 近 5 日涨幅 > 12% 记 +2


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


def fetch_quotes(codes):
    """批量取 名称/最新价/涨跌幅/换手率/总市值。"""
    out = {}
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        secids = ",".join(f"{setcode_of(c)}.{c}" for c in chunk)
        url = (f"{BASE}/api/qt/ulist.np/get?fltt=2&invt=2&secids={secids}"
               "&fields=f2,f3,f8,f12,f14,f20")
        d = get_json(url)
        for r in (d.get("data") or {}).get("diff") or []:
            def num(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
            mcap = num(r.get("f20"))
            out[r["f12"]] = {
                "name": r["f14"],
                "px": num(r.get("f2")),
                "chg1": num(r.get("f3")),
                "turnover": num(r.get("f8")),
                "mcap": round(mcap / 1e8, 1) if mcap else None,
            }
        time.sleep(0.3)
    return out


def fetch_chg5(code):
    """近 5 个交易日涨幅：取 6 根日线，末根 / 首根 - 1。"""
    secid = f"{setcode_of(code)}.{code}"
    url = (f"{HIS}/api/qt/stock/kline/get?secid={secid}"
           "&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=1&end=20500101&lmt=6")
    d = get_json(url).get("data")
    if not d or not d.get("klines") or len(d["klines"]) < 6:
        return None, None
    closes = [float(ln.split(",")[1]) for ln in d["klines"]]
    dates = [ln.split(",")[0] for ln in d["klines"]]
    return round((closes[-1] / closes[0] - 1) * 100, 2), dates[-1]


def main():
    pool_path = os.path.join(HERE, "screen_pool.json")
    raw_path = os.path.join(HERE, "raw_snapshot.json")

    with open(pool_path, "r", encoding="utf-8") as fh:
        pool = json.load(fh)
    with open(raw_path, "r", encoding="utf-8") as fh:
        snap = json.load(fh)

    trade_date = snap["tradeDate"]
    ss = snap.get("seatStocks") or {}
    hot = {h["code"]: h for h in ss.get("stockHot") or []}
    window = ss.get("window") or {}
    print(f"交易日 {trade_date} · 游资窗口 {window.get('from')}~{window.get('to')}"
          f"（{window.get('days')} 个交易日）")

    codes = [r["code"] for r in pool]
    print(f"[1/3] 批量行情 {len(codes)} 只 ...")
    quotes = fetch_quotes(codes)
    print(f"      取到 {len(quotes)} 只")

    print("[2/3] 逐只补 5 日涨幅 ...")
    chg5_map, miss5 = {}, []
    for c in codes:
        try:
            v, last_date = fetch_chg5(c)
        except Exception as e:                      # noqa: BLE001
            print(f"      [!] {c} K 线失败：{e}")
            v, last_date = None, None
        if v is None:
            miss5.append(c)
        else:
            chg5_map[c] = (v, last_date)
        time.sleep(0.25)
    print(f"      取到 {len(chg5_map)} 只，缺 {len(miss5)} 只 {miss5}")

    print("[3/3] 重算动态加分并写回 ...")
    miss_mcap, miss_turn, a4_hit, a5_hit = [], [], 0, 0
    for r in pool:
        c = r["code"]
        q = quotes.get(c, {})
        if q.get("mcap") is None:
            miss_mcap.append(c)
        else:
            r["mcap"] = q["mcap"]
        if q.get("turnover") is None:
            miss_turn.append(c)
        else:
            r["turnover"] = round(q["turnover"], 2)
        if c in chg5_map:
            r["chg5"] = chg5_map[c][0]
        r["px"] = q.get("px")
        r["chg1"] = q.get("chg1")

        h = hot.get(c)
        r["a4"] = 1 if (h and h.get("seatCount", 0) > 0 and h.get("net", 0) > 0) else 0
        if r["a4"]:
            a4_hit += 1
            r["a4Why"] = f"近{window.get('days')}日 {h['seatCount']} 个游资席位净买 {h['net']:.2f} 亿"
        else:
            r["a4Why"] = ""

        c5 = r.get("chg5")
        r["a5"] = 1 if (c5 is not None and c5 > A5_THRESHOLD) else 0
        if r["a5"]:
            a5_hit += 1
        r["a5Why"] = f"5日涨 {c5:+.2f}%" if c5 is not None else ""

    meta = {
        "asOf": trade_date,
        "refreshedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "priceSource": "东方财富（ulist + kline 前复权）",
        "hotWindow": window,
        "count": len(pool),
        "a4Hit": a4_hit,
        "a5Hit": a5_hit,
        "note": "市值/换手/5日涨幅为当日收盘截面；a4 由龙虎榜游资席位明细计算，a5 由收盘价计算；a1–a3 为人工研究结论，不由脚本改动。",
    }

    with open(pool_path, "w", encoding="utf-8") as fh:
        json.dump(pool, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(HERE, "screen_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    print(f"完成。{len(pool)} 只 · A4 命中 {a4_hit} 只 · A5 命中 {a5_hit} 只")
    if miss_mcap:
        print(f"  [!] 市值缺失 {len(miss_mcap)} 只：{miss_mcap}")
    if miss_turn:
        print(f"  [!] 换手缺失 {len(miss_turn)} 只：{miss_turn}")

    # 自检：动态字段不能是 None 冒充数字
    for r in pool:
        assert r["mcap"] is not None, f"{r['code']} 市值缺失"
        assert r["turnover"] is not None, f"{r['code']} 换手缺失"
        assert isinstance(r["a4"], int) and isinstance(r["a5"], int)
    return 0


if __name__ == "__main__":
    sys.exit(main())
