# -*- coding: utf-8 -*-
"""
潮汐 · 个股分析焦点池（筛选 Top N）
====================================
以前 watchlist 是写死的 5 只票，和「股票筛选」完全脱节。
现在改为：按 screen_rules.json 的默认预设跑一遍筛选，取前 N 名，
再抓这 N 只的当日行情与 60 日收盘序列，写进 raw_snapshot.watchlist。

于是一旦筛选条件或候选池行情变化，个股分析页面自动跟着换标的——
这就是「个股分析跟着筛选走」的落地方式。

参数：
    --preset default   使用哪个预设（默认 default）
    --n 5              取前几名（覆盖预设里的 topN）

输出：raw_snapshot.json 的 watchlist 字段（其余字段不动）
      top5.json（前端标注用，记录入选理由）
"""

import argparse
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

KLINE_N = 60


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


# ---------------------------------------------------------------- 筛选逻辑
def score_of(row, rules):
    sr = rules["scoreRules"]
    s = 0
    for k in ("a1", "a2", "a3"):
        s += (row.get(k) or 0) * sr[k]
    s += (row.get("a4") or 0) * sr["a4"]
    s += (row.get("a5") or 0) * sr["a5"]
    return s


def passes(row, preset, rules):
    code = row["code"]
    for p in preset.get("excludePrefix") or []:
        if code.startswith(p):
            return False, "板块排除"
    mcap = row.get("mcap")
    turn = row.get("turnover")
    if mcap is None or turn is None:
        return False, "行情缺失"
    if not (preset["mcapMin"] <= mcap <= preset["mcapMax"]):
        return False, "市值越界"
    if turn <= preset["turnoverMin"]:
        return False, "换手不足"
    if preset.get("tracks"):
        if row.get("track") not in preset["tracks"]:
            return False, "赛道不符"
    if preset.get("requireA4") and not row.get("a4"):
        return False, "无游资足迹"
    s = score_of(row, rules)
    if s < preset.get("minScore", 0):
        return False, "加分不足"
    return True, ""


def rank(pool, rules, preset):
    rows = []
    for r in pool:
        ok, why = passes(r, preset, rules)
        if not ok:
            continue
        rows.append({"row": r, "score": score_of(r, rules)})
    rows.sort(key=lambda x: (-x["score"], x["row"]["mcap"]))
    return rows


# ---------------------------------------------------------------- 行情抓取
def fetch_quote(code):
    url = (f"{BASE}/api/qt/stock/get?secid={setcode_of(code)}.{code}"
           "&fltt=2&invt=2&fields=f43,f57,f58,f168,f170")
    d = get_json(url).get("data")
    if not d:
        raise RuntimeError(f"{code} 行情为空")

    def num(v, nd=2):
        try:
            return round(float(v), nd)
        except (TypeError, ValueError):
            return None

    return {"price": num(d.get("f43")), "pct": num(d.get("f170")),
            "turnoverRate": num(d.get("f168")), "name": d.get("f58")}


def fetch_kline(code, n=KLINE_N):
    url = (f"{HIS}/api/qt/stock/kline/get?secid={setcode_of(code)}.{code}"
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1"
           f"&end=20500101&lmt={n}")
    d = get_json(url).get("data")
    if not d or not d.get("klines"):
        raise RuntimeError(f"{code} K 线为空")
    return [{"d": ln.split(",")[0].replace("-", ""), "c": round(float(ln.split(",")[2]), 2)}
            for ln in d["klines"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default=None)
    ap.add_argument("--n", type=int, default=None)
    args = ap.parse_args()

    with open(os.path.join(HERE, "screen_rules.json"), "r", encoding="utf-8") as fh:
        rules = json.load(fh)
    with open(os.path.join(HERE, "screen_pool.json"), "r", encoding="utf-8") as fh:
        pool = json.load(fh)
    snap_path = os.path.join(HERE, "raw_snapshot.json")
    with open(snap_path, "r", encoding="utf-8") as fh:
        snap = json.load(fh)

    pid = args.preset or rules.get("default", "default")
    preset = next((p for p in rules["presets"] if p["id"] == pid), rules["presets"][0])
    top_n = args.n or preset.get("topN", 5)

    ranked = rank(pool, rules, preset)
    print(f"预设「{preset['name']}」通过 {len(ranked)} 只，取前 {top_n}")
    picked = ranked[:top_n]
    if not picked:
        raise SystemExit("筛选结果为空，无法生成焦点池")

    trade_date = snap["tradeDate"]
    print(f"抓取 {len(picked)} 只的行情与 {KLINE_N} 日 K 线 ...")
    watch, summary = [], []
    for item in picked:
        r = item["row"]
        code = r["code"]
        q = fetch_quote(code)
        kl = fetch_kline(code)
        if len(kl) != KLINE_N:
            print(f"  [!] {code} K 线只有 {len(kl)} 根（停牌或次新），按实际根数保留")
        last_d = kl[-1]["d"]
        if last_d != trade_date.replace("-", ""):
            print(f"  [!] {code} 末根 K 线 {last_d} != {trade_date}（可能停牌）")
        watch.append({
            "code": code,
            "setcode": setcode_of(code),
            "name": r["name"],
            "industry": r["track"],
            "price": q["price"],
            "pct": q["pct"],
            "turnoverRate": q["turnoverRate"],
            "kline60": kl,
            # 入选理由（前端直接展示，不重算）
            "score": item["score"],
            "rank": len(summary) + 1,
            "mcap": r["mcap"],
            "chg5": r.get("chg5"),
            "a4": r.get("a4"),
            "a5": r.get("a5"),
            "a4Why": r.get("a4Why") or "",
            "note": r.get("note") or "",
            "risk": r.get("risk") or "",
            "track": r["track"],
        })
        summary.append({"code": code, "name": r["name"], "score": item["score"],
                        "mcap": r["mcap"], "track": r["track"]})
        print(f"  #{len(summary)} {code} {r['name']:<6} 分数 {item['score']} · "
              f"{r['track']} · {len(kl)} 根 {kl[0]['d']}→{kl[-1]['d']} · "
              f"现价 {q['price']} ({q['pct']:+.2f}%)")
        time.sleep(0.4)

    snap["watchlist"] = watch
    with open(snap_path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=2)

    meta = {
        "asOf": trade_date,
        "presetId": preset["id"],
        "presetName": preset["name"],
        "presetNote": preset["note"],
        "topN": top_n,
        "passed": len(ranked),
        "picked": summary,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(os.path.join(HERE, "top5.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(f"完成。watchlist 已替换为 {len(watch)} 只，写入 top5.json")

    assert all(w["kline60"] and w["kline60"][-1]["d"] for w in watch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
