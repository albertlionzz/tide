# -*- coding: utf-8 -*-
"""
妖股量化 · 样本层 · 事件级特征表

设计取舍（重要）：
    全市场 5579 只 × 约 1400 个交易日 ≈ 780 万行。如果按「个股-日」建宽表，
    光 float32 就要 900 MB 以上，后续每次进化都要重读，不现实。
    但模型真正要判断的时刻只有几个：首板日收盘、断板日收盘、潜伏候选日收盘。
    这类事件总共十几万条。所以走「两遍扫描」：

    第一遍：全市场逐只算涨停/连板，聚合每日市场环境（涨停家数、连板结构、
            上涨占比、情绪热度）。这是「今天该不该出手」的环境变量。
    第二遍：再逐只算滚动特征，只存事件日的特征值 + 未来 20 日归一化价格路径。

事件类型（三种动作各自的候选集）：
    firstboard  lb==1 且非跌停                       → 打板动作
    rebound     前一日涨停且连板≥2、今日未涨停        → 断板反包动作
    pre         近 20 日内 ≥2 次涨停、今日未涨停      → 潜伏动作（预测次日首板）

前瞻字段（全部以「次日开盘价」为基准归一化，不同价位的票才能放一起比）：
    e_open     次日开盘价（买入价基准）
    lim_up_o   次日一字涨停（开盘即涨停 → 打板买不进，回测须弃单）
    lim_dn_o   次日一字跌停（开盘即跌停 → 反包也不该买）
    f_c/f_h/f_l  未来 1..20 日 收盘/最高/最低 ÷ e_open - 1
    f_lu       未来 1..5 日是否涨停
    f_lbmax    未来 20 日最大连板（用于妖股分层）

输出：data/events.pkl、data/market_env.pkl
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import time

import numpy as np
import pandas as pd

import limits

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
UNIVERSE = os.path.join(DATA, "universe.json")
OUT_EV = os.path.join(DATA, "events.pkl")
OUT_ENV = os.path.join(DATA, "market_env.pkl")

H = 20                      # 前瞻窗口
START = "2021-01-01"
MIN_BARS = 120              # 上市不足 120 个交易日的不入样本（次新股特征不稳）

# 一字板判定阈值：开盘涨幅达到「涨停阈值 - 0.4」即视为开盘即涨停
LIM_OPEN_THR = {"main": 9.4, "chinext": 19.4, "star": 19.4}
LIM_OPEN_ST = 4.4

FEAT_COLS = [
    "pct", "turn", "gap", "close_pos", "amp", "seal_i",
    "dist_ma5", "dist_ma10", "dist_ma20", "vol_ratio", "turn_ratio",
    "dist_high20", "dist_high60", "pct_3d", "pct_5d", "pct_20d",
    "log_amt", "lu20", "amt_ratio",
]
ENV_COLS = ["lu", "ld", "up_ratio", "lbmax", "lu_heat", "cycle", "lu_ld", "amt"]


def load_meta() -> dict[str, dict]:
    with open(UNIVERSE, "r", encoding="utf-8") as fh:
        return {x["code"]: x for x in json.load(fh)["items"]}


def iter_stocks():
    """按文件名稳定遍历；跳过北交所（30% 涨跌幅，与主板不可同模）。"""
    meta = load_meta()
    for f in sorted(x for x in os.listdir(RAW) if x.endswith(".json.gz")):
        code = f.split(".")[0]
        m = meta.get(code) or {}
        if m.get("board") == "bj":
            continue
        try:
            with gzip.open(os.path.join(RAW, f), "rt", encoding="utf-8") as fh:
                obj = json.load(fh)
        except Exception:                          # noqa: BLE001
            continue
        yield code, m.get("name") or obj.get("name", ""), m.get("board", "main"), \
            bool(m.get("st")), obj["rows"]


def roll(a: np.ndarray, n: int, how: str = "mean") -> np.ndarray:
    """滚动统计，min_periods 放宽到 n//2，前置 NaN（不引入未来信息）。"""
    s = pd.Series(a, dtype="float64")
    return getattr(s.rolling(n, min_periods=max(2, n // 2)), how)().to_numpy(dtype="float32")


def prev(a: np.ndarray, n: int = 1) -> np.ndarray:
    """右移 n 位（取过去值）。"""
    out = np.full(a.shape, np.nan, dtype="float32")
    if n < len(a):
        out[n:] = a[:-n].astype("float32")
    return out


# ---------------------------------------------------------------- 第一遍

def pass_market() -> pd.DataFrame:
    acc: dict[str, dict] = {}
    n, t0 = 0, time.time()
    for code, name, board, st, rows in iter_stocks():
        for r in limits.annotate(rows, board, st):
            if r["d"] < START:
                continue
            a = acc.get(r["d"])
            if a is None:
                a = acc[r["d"]] = {"lu": 0, "ld": 0, "up": 0, "total": 0, "lb2": 0,
                                   "lb3": 0, "lb5": 0, "lbmax": 0, "amt": 0.0,
                                   "turn": []}
            a["total"] += 1
            a["lu"] += r["lu"]
            a["ld"] += r["ld"]
            a["up"] += r["pct"] > 0
            lb = r["lb"]
            a["lb2"] += lb >= 2
            a["lb3"] += lb >= 3
            a["lb5"] += lb >= 5
            a["lbmax"] = max(a["lbmax"], lb)
            a["amt"] += r["a"]
            if r["turn"] is not None:
                a["turn"].append(r["turn"])
        n += 1
        if n % 1000 == 0:
            print(f"    第一遍 {n} 只 / {len(acc)} 个交易日  {time.time() - t0:.0f}s", flush=True)

    rows = [{"date": d, "lu": a["lu"], "ld": a["ld"], "up": a["up"], "total": a["total"],
             "lb2": a["lb2"], "lb3": a["lb3"], "lb5": a["lb5"], "lbmax": a["lbmax"],
             "amt": round(a["amt"] / 1e8, 1),
             "turn_med": round(float(np.median(a["turn"])), 2) if a["turn"] else np.nan}
            for d, a in acc.items()]
    env = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    env["up_ratio"] = (env["up"] / env["total"] * 100).round(2)
    env["lu_ld"] = (env["lu"] / env["ld"].replace(0, np.nan)).round(2)
    env["lu_ma20"] = env["lu"].rolling(20, min_periods=5).mean().round(1)
    env["lu_heat"] = (env["lu"] / env["lu_ma20"]).round(2)
    env["ld_ma20"] = env["ld"].rolling(20, min_periods=5).mean().round(1)
    env["lu_m5"] = env["lu"].rolling(5, min_periods=3).mean()
    # 情绪周期：涨停家数 5 日均 ÷ 20 日均，>1 回暖、<1 退潮
    env["cycle"] = (env["lu_m5"] / env["lu_ma20"]).round(3)
    print(f"  市场环境 {len(env)} 个交易日（{env['date'].min()} ~ {env['date'].max()}）")
    return env


# ---------------------------------------------------------------- 第二遍

def tier_of(max_lb: int, ret: float) -> str:
    """S/A = 妖股（严格口径），B = 次级，N = 不足以称为妖股。"""
    if max_lb >= 5 or ret >= 100:
        return "S"
    if max_lb >= 3 or ret >= 50:
        return "A"
    if max_lb >= 2 or ret >= 30:
        return "B"
    return "N"


def pass_events(env: pd.DataFrame) -> pd.DataFrame:
    env_idx = {d: i for i, d in enumerate(env["date"].tolist())}
    env_arr = {c: env[c].to_numpy(dtype="float32") for c in ENV_COLS}

    recs: list[dict] = []
    n, t0 = 0, time.time()
    for code, name, board, st, rows in iter_stocks():
        ser = limits.annotate(rows, board, st)
        m = len(ser)
        if m < MIN_BARS:
            continue
        d = [r["d"] for r in ser]
        o = np.array([r["o"] for r in ser], dtype="float32")
        h = np.array([r["h"] for r in ser], dtype="float32")
        lo = np.array([r["l"] for r in ser], dtype="float32")
        c = np.array([r["c"] for r in ser], dtype="float32")
        v = np.array([r["v"] for r in ser], dtype="float32")
        amt = np.array([r["a"] for r in ser], dtype="float32")
        pct = np.array([r["pct"] for r in ser], dtype="float32")
        turn = np.array([r["turn"] if r["turn"] is not None else np.nan for r in ser],
                        dtype="float32")
        lu = np.array([1 if r["lu"] else 0 for r in ser], dtype="int8")
        ld = np.array([1 if r["ld"] else 0 for r in ser], dtype="int8")
        lb = np.array([r["lb"] for r in ser], dtype="int16")
        gap = np.array([r["gap"] if r["gap"] is not None else np.nan for r in ser],
                       dtype="float32")
        seal = np.array([1 if r["seal"] else 0 for r in ser], dtype="int8")

        ma5, ma10, ma20 = roll(c, 5), roll(c, 10), roll(c, 20)
        vma5, tma5, ama5 = roll(v, 5), roll(turn, 5), roll(amt, 5)
        h20, h60 = roll(h, 20, "max"), roll(h, 60, "max")
        lu20 = roll(lu.astype("float32"), 20, "sum")

        with np.errstate(divide="ignore", invalid="ignore"):
            feats = {
                "pct": pct, "turn": turn, "gap": gap,
                "close_pos": ((c - lo) / np.where(h - lo == 0, np.nan, h - lo)),
                "amp": ((h - lo) / prev(c, 1) * 100),
                "seal_i": seal.astype("float32"),
                "dist_ma5": (c / ma5 * 100 - 100),
                "dist_ma10": (c / ma10 * 100 - 100),
                "dist_ma20": (c / ma20 * 100 - 100),
                "vol_ratio": (v / vma5),
                "turn_ratio": (turn / tma5),
                "dist_high20": (c / h20 * 100 - 100),
                "dist_high60": (c / h60 * 100 - 100),
                "pct_3d": (c / prev(c, 3) * 100 - 100),
                "pct_5d": (c / prev(c, 5) * 100 - 100),
                "pct_20d": (c / prev(c, 20) * 100 - 100),
                "log_amt": np.log1p(amt),
                "lu20": lu20,
                "amt_ratio": (amt / ama5),
            }
            feats = {k: np.asarray(vv, dtype="float32") for k, vv in feats.items()}

        # 次日开盘与一字板标记
        nx_o = np.full(m, np.nan, dtype="float32")
        nx_o[:-1] = o[1:]
        thr = LIM_OPEN_ST if st else LIM_OPEN_THR.get(board, 9.4)
        gap_nx = np.full(m, np.nan, dtype="float32")
        with np.errstate(divide="ignore", invalid="ignore"):
            gap_nx[:-1] = (o[1:] / np.where(c[:-1] == 0, np.nan, c[:-1]) - 1) * 100
        lim_up_o = (gap_nx >= thr).astype("int8")
        lim_dn_o = (gap_nx <= -thr).astype("int8")
        lim_up_o[~np.isfinite(gap_nx)] = 0
        lim_dn_o[~np.isfinite(gap_nx)] = 0

        # 前瞻路径
        fc = np.full((m, H), np.nan, dtype="float32")
        fh = np.full((m, H), np.nan, dtype="float32")
        fl = np.full((m, H), np.nan, dtype="float32")
        flu = np.zeros((m, H), dtype="int8")
        base = nx_o[:, None]
        valid_base = np.isfinite(nx_o) & (nx_o > 0)
        for k in range(1, H + 1):
            idx = np.arange(m) + k
            ok = (idx < m) & valid_base
            if not ok.any():
                break
            src, dst, b = idx[ok], np.arange(m)[ok], base[ok, 0]
            fc[dst, k - 1] = c[src] / b - 1
            fh[dst, k - 1] = h[src] / b - 1
            fl[dst, k - 1] = lo[src] / b - 1
            if k <= 5:
                flu[dst, k - 1] = lu[src]

        # 未来 20 日最大连板
        lbmax_f = np.zeros(m, dtype="int16")
        pad = np.concatenate([lb.astype("int16"), np.zeros(H, dtype="int16")])
        for k in range(1, H + 1):
            np.maximum(lbmax_f, pad[k:k + m], out=lbmax_f)

        lu_prev = np.concatenate(([0], lu[:-1])).astype("int8")
        lb_prev = np.concatenate(([0], lb[:-1])).astype("int16")
        masks = {
            "firstboard": (lb == 1) & (ld == 0),
            "rebound": (lb == 0) & (lu_prev == 1) & (lb_prev >= 2) & (ld == 0),
            "pre": (lu20 >= 2) & (lb == 0) & (ld == 0),
        }

        for kind, mask in masks.items():
            for i in np.flatnonzero(mask):
                if d[i] < START or i + 1 >= m or not valid_base[i]:
                    continue
                mx = float(np.nanmax(fc[i])) if np.isfinite(fc[i]).any() else 0.0
                rec = {"code": code, "name": name, "board": board, "st": st,
                       "date": d[i], "kind": kind,
                       "mx_lb": int(lbmax_f[i]), "mx_ret": round(mx * 100, 2),
                       "tier": tier_of(int(lbmax_f[i]), mx * 100) if kind != "pre" else "",
                       "e_open": float(nx_o[i]), "lim_up_o": int(lim_up_o[i]),
                       "lim_dn_o": int(lim_dn_o[i]),
                       "lb": int(lb[i]), "lb_prev": int(lb_prev[i]),
                       "lu": int(lu[i]),
                       "f_c": fc[i].tolist(), "f_h": fh[i].tolist(),
                       "f_l": fl[i].tolist(), "f_lu": flu[i].tolist()}
                for cn in FEAT_COLS:
                    rec[cn] = float(feats[cn][i]) if np.isfinite(feats[cn][i]) else np.nan
                ei = env_idx.get(d[i])
                if ei is not None:
                    for cn in ENV_COLS:
                        rec["env_" + cn] = float(env_arr[cn][ei])
                recs.append(rec)
        n += 1
        if n % 500 == 0:
            print(f"    第二遍 {n} 只 / {len(recs)} 条事件  {time.time() - t0:.0f}s", flush=True)

    ev = pd.DataFrame(recs)
    print(f"  事件合计 {len(ev)} 条：" + "，".join(
        f"{k}={v}" for k, v in ev["kind"].value_counts().items()))
    return ev


def main() -> int:
    t0 = time.time()
    if not os.path.isdir(RAW) or not any(f.endswith(".json.gz") for f in os.listdir(RAW)):
        print("data/raw 为空，先跑 fetch_daily.py")
        return 1
    print("第一遍：全市场逐只算涨停/连板，聚合市场环境…")
    env = pass_market()
    env.to_pickle(OUT_ENV)
    print("第二遍：抽事件级特征…")
    ev = pass_events(env)
    ev.to_pickle(OUT_EV)
    print(f"OK -> {os.path.basename(OUT_EV)}({len(ev)}) / "
          f"{os.path.basename(OUT_ENV)}({len(env)})  耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
