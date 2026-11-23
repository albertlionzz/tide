# -*- coding: utf-8 -*-
"""
妖股量化 · 标签层 · 妖股事件识别与分层

定义（可调，全部落在 config 里）：
    启动日 T0：当日首板（lb==1），且前 20 日涨幅 < 30%（排除接力中段）
    妖股事件：T0 起 20 个交易日内，最大连板 ≥ 2 或 区间涨幅 ≥ 30%
    分层：S = 最大连板 ≥5 或 区间涨幅 ≥100%
          A = 最大连板 3-4 或 区间涨幅 50-100%
          B = 最大连板 2   或 区间涨幅 30-50%
    见顶：事件内最高收盘价；之后 3 日不创新高即视为结束

为什么不直接按「连续涨停」数：大量妖股是「涨停-震荡-再涨停」的接力形态，
中途会断板。按「20 日窗口内的峰值涨幅 + 最大连板」双条件，能覆盖两种形态。
"""

from __future__ import annotations

TIER_CFG = {
    "S": {"min_lb": 5, "min_ret": 100.0},
    "A": {"min_lb": 3, "min_ret": 50.0},
    "B": {"min_lb": 2, "min_ret": 30.0},
}


def _tier(max_lb: int, ret: float) -> str | None:
    if max_lb >= TIER_CFG["S"]["min_lb"] or ret >= TIER_CFG["S"]["min_ret"]:
        return "S"
    if max_lb >= TIER_CFG["A"]["min_lb"] or ret >= TIER_CFG["A"]["min_ret"]:
        return "A"
    if max_lb >= TIER_CFG["B"]["min_lb"] or ret >= TIER_CFG["B"]["min_ret"]:
        return "B"
    return None


def detect(series: list[dict], look: int = 20, pre_cap: float = 30.0) -> list[dict]:
    """从带标注的日线序列里找出所有妖股事件。

    返回每项：
        t0 / t0i     启动日（首板）日期与下标
        peak / peaki 区间最高收盘
        endi         事件结束下标（见顶后 3 日）
        max_lb       区间最大连板
        ret          启动前收盘 → 区间最高收盘 的涨幅（%）
        tier         S / A / B
        dur          事件长度（交易日）
    """
    n = len(series)
    events, i = [], 0
    while i < n:
        row = series[i]
        if not (row["lb"] == 1 and not row["ld"]):
            i += 1
            continue

        # 前置条件：不是已经在高位接力
        base_i = max(0, i - 20)
        base = series[base_i]["c"]
        pre_ret = (row["c"] / base - 1) * 100 if base else 0.0
        if pre_ret >= pre_cap:
            i += 1
            continue

        j_end = min(i + look, n - 1)
        max_lb, peak, peak_i = 1, row["c"], i
        for k in range(i, j_end + 1):
            if series[k]["lb"] > max_lb:
                max_lb = series[k]["lb"]
            if series[k]["c"] > peak:
                peak, peak_i = series[k]["c"], k

        ref = series[i - 1]["c"] if i > 0 else series[base_i]["c"]
        ret = (peak / ref - 1) * 100 if ref else 0.0
        tier = _tier(max_lb, ret)
        if tier is None:
            i += 1
            continue

        # 见顶后第 3 个交易日视为事件结束（给后续动作留出观察窗）
        end_i = min(peak_i + 3, n - 1)
        events.append({
            "t0": row["d"], "t0i": i,
            "peak": peak, "peaki": peak_i, "endi": end_i,
            "max_lb": max_lb, "ret": round(ret, 2), "tier": tier,
            "dur": end_i - i + 1,
        })
        i = peak_i + 1          # 跳过本段，避免同一波被判成多个事件
    return events


def summarize(events: list[dict]) -> dict:
    by = {"S": 0, "A": 0, "B": 0}
    for e in events:
        by[e["tier"]] = by.get(e["tier"], 0) + 1
    rets = [e["ret"] for e in events]
    return {
        "total": len(events),
        "byTier": by,
        "avgRet": round(sum(rets) / len(rets), 2) if rets else 0,
        "maxRet": round(max(rets), 2) if rets else 0,
        "avgDur": round(sum(e["dur"] for e in events) / len(events), 1) if events else 0,
    }
