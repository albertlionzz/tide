# -*- coding: utf-8 -*-
"""
潮汐 · 日线统一入口（东财优先，腾讯兜底）
==========================================
2026-11-18 起东财 `push2his` 会对高频请求直接断连（RemoteDisconnected），
盘中更严重。原先每个脚本各写一份 K 线抓取，一处被封就要改四处；
统一到这里，只维护一套降级逻辑。

约定：
- 目标交易日由调用方传入（一般取 raw_snapshot.tradeDate），**不用「今天」**。
  盘中跑脚本时「今天」那根还没收盘，混进去会让区间涨幅、均线全错。
- 只返回 `d`(YYYYMMDD) / `c`(收盘) / `v`(成交量·手) / `a`(成交额·亿元)。
  腾讯日线没有成交额字段，`a` 置 0 —— 不编数。
"""

import json
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

EM_HIS = "http://push2his.eastmoney.com"
QQ_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _get(url, referer, retries=2, timeout=20):
    last = None
    for i in range(retries):
        for u in (url, url.replace("http://", "https://")):
            try:
                req = urllib.request.Request(
                    u, headers={"User-Agent": UA, "Accept": "*/*", "Referer": referer})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:                 # noqa: BLE001
                last = e
        time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"拉取失败 {url[:70]} -> {last}")


def setcode_of(code, override=None):
    if override is not None:
        return str(override)
    if code.startswith(("6", "9")):
        return "1"
    if code.startswith(("4", "8")):
        return "2"
    return "0"


def _mkt_of(code, setcode):
    sc = setcode_of(code, setcode)
    return {"1": "sh", "2": "bj"}.get(sc, "sz")


def fetch_kline(code, setcode=None, n=60, trade_date=None, want_amount=True):
    """返回最近 n 根的日线（截至 trade_date 含当日）。trade_date 形如 2026-11-18。"""
    limit = (trade_date or "").replace("-", "")

    def clip(rows):
        if limit:
            rows = [r for r in rows if r["d"] <= limit]
        return rows[-n:]

    try:
        sc = setcode_of(code, setcode)
        url = (f"{EM_HIS}/api/qt/stock/kline/get?secid={sc}.{code}"
               "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1"
               f"&end={limit or '20500101'}&lmt={n}")
        d = _get(url, "https://quote.eastmoney.com/", retries=1).get("data")
        if d and d.get("klines"):
            out = []
            for ln in d["klines"]:
                p = ln.split(",")
                out.append({"d": p[0].replace("-", ""), "c": round(float(p[2]), 2),
                            "v": int(float(p[5] or 0)),
                            "a": round(float(p[6] or 0) / 1e8, 3)})
            rows = clip(out)
            if rows:
                return rows
    except Exception:                              # noqa: BLE001
        pass

    mkt = _mkt_of(code, setcode)
    d = (_get(f"{QQ_KLINE}?param={mkt}{code},day,,,{n + 5},qfq",
              "https://gu.qq.com/") or {}).get("data") or {}
    node = d.get(f"{mkt}{code}") or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise RuntimeError(f"{code} K 线为空（东财+腾讯均无）")
    out = [{"d": r[0].replace("-", ""), "c": round(float(r[2]), 2),
            "v": int(float(r[5] or 0)), "a": 0.0} for r in rows]
    return clip(out)
