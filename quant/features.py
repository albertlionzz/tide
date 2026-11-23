# -*- coding: utf-8 -*-
"""
妖股量化 · 特征层 · 构建特征表

两类特征：
    A. 市场环境（每日一个值，全市场横截面聚合）—— 决定「今天该不该出手」
    B. 个股特征（每只每日）—— 决定「买哪只」

全部只用 T 日及之前的数据，**不含任何未来信息**（回测防未来函数的第一道闸）。

输出：
    data/features.pkl     个股特征（pandas DataFrame）
    data/daily_market.pkl 市场环境（按日期索引）
"""

from __future__ import annotations

import os
import sqlite3
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "market.db")
OUT_F = os.path.join(HERE, "data", "features.pkl")
OUT_M = os.path.join(HERE, "data", "daily_market.pkl")

START = "2015-01-01"          # 妖股研究起点；2015 年前数据保留在库里但不入特征


def load_bars() -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT code,name,board,date,o,h,l,c,v,a,pct,turn,lu,ld,lb,gap,seal "
        "FROM bars WHERE date >= ? ORDER BY code, date", con, params=(START,))
    con.close()
    for col in ("o", "h", "l", "c", "v", "a", "pct", "turn", "gap"):
        df[col] = df[col].astype("float32")
    for col in ("lu", "ld", "lb", "seal"):
        df[col] = df[col].astype("int16")
    print(f"  读入 {len(df):,} 行 / {df['code'].nunique()} 只 / {df['date'].min()}~{df['date'].max()}")
    return df


def build_market(df: pd.DataFrame) -> pd.DataFrame:
    """全市场横截面：每日涨停/跌停家数、连板结构、宽度。"""
    g = df.groupby("date", sort=True)
    m = pd.DataFrame({
        "lu": g["lu"].sum(),
        "ld": g["ld"].sum(),
        "total": g["code"].count(),
        "lb_max": g["lb"].max(),
        "lb2": g["lb"].apply(lambda s: int((s >= 2).sum())),
        "lb3": g["lb"].apply(lambda s: int((s >= 3).sum())),
        "lb5": g["lb"].apply(lambda s: int((s >= 5).sum())),
        "up": g["pct"].apply(lambda s: int((s > 0).sum())),
        "turn_med": g["turn"].median(),
        "amt_sum": g["a"].sum(),
    })
    m["up_ratio"] = (m["up"] / m["total"] * 100).round(2)
    m["lu_ld"] = (m["lu"] / m["ld"].replace(0, np.nan)).round(2)
    # 情绪温度：涨停家数相对近 20 日均值的倍数
    m["lu_ma20"] = m["lu"].rolling(20).mean().round(1)
    m["lu_heat"] = (m["lu"] / m["lu_ma20"]).round(2)
    m["ld_ma20"] = m["ld"].rolling(20).mean().round(1)
    print(f"  市场环境 {len(m)} 个交易日")
    return m


def build_stock(df: pd.DataFrame) -> pd.DataFrame:
    """个股时序特征（按 code 分组滚动，全部左对齐，只用历史）。"""
    df = df.sort_values(["code", "date"]).copy()
    g = df.groupby("code", group_keys=False)

    def shift(col, n):
        return g[col].shift(n)

    df["pct_3d"] = ((df["c"] / shift("c", 3) - 1) * 100).astype("float32")
    df["pct_5d"] = ((df["c"] / shift("c", 5) - 1) * 100).astype("float32")
    df["pct_20d"] = ((df["c"] / shift("c", 20) - 1) * 100).astype("float32")

    df["ma5"] = g["c"].transform(lambda s: s.rolling(5).mean())
    df["ma10"] = g["c"].transform(lambda s: s.rolling(10).mean())
    df["ma20"] = g["c"].transform(lambda s: s.rolling(20).mean())
    df["dist_ma5"] = ((df["c"] / df["ma5"] - 1) * 100).astype("float32")
    df["dist_ma10"] = ((df["c"] / df["ma10"] - 1) * 100).astype("float32")
    df["dist_ma20"] = ((df["c"] / df["ma20"] - 1) * 100).astype("float32")

    df["vol_ma5"] = g["v"].transform(lambda s: s.rolling(5).mean())
    df["vol_ratio"] = (df["v"] / df["vol_ma5"]).astype("float32")
    df["turn_ma5"] = g["turn"].transform(lambda s: s.rolling(5).mean())
    df["turn_ratio"] = (df["turn"] / df["turn_ma5"]).astype("float32")

    df["high20"] = g["h"].transform(lambda s: s.rolling(20).max())
    df["dist_high20"] = ((df["c"] / df["high20"] - 1) * 100).astype("float32")
    df["high60"] = g["h"].transform(lambda s: s.rolling(60).max())
    df["new_high60"] = (df["c"] >= df["high60"] * 0.999).astype("int8")

    # 收盘在当日振幅中的位置：1 = 收在最高（封板/强势），0 = 收在最低
    rng = (df["h"] - df["l"]).replace(0, np.nan)
    df["close_pos"] = ((df["c"] - df["l"]) / rng).astype("float32")
    df["amp"] = (((df["h"] - df["l"]) / shift("c", 1)) * 100).astype("float32")

    # 连板阶段标记
    df["first_board"] = ((df["lb"] == 1) & (df["lu"] == 1)).astype("int8")
    df["lb_prev"] = g["lb"].shift(1).fillna(0).astype("int16")

    df["log_amt"] = np.log1p(df["a"]).astype("float32")
    return df


def main() -> int:
    t0 = time.time()
    df = load_bars()
    mkt = build_market(df)
    feat = build_stock(df)
    mkt.to_pickle(OUT_M)
    feat.to_pickle(OUT_F)
    print(f"OK -> {OUT_M.name} / {OUT_F.name}  耗时 {time.time() - t0:.0f}s")

    print("\n市场环境样本（最近 6 日）：")
    cols = ["lu", "ld", "lb_max", "lb3", "up_ratio", "lu_heat"]
    print(mkt[cols].tail(6).to_string())

    print("\n个股特征缺失率（前 10 高）：")
    miss = feat[["pct_5d", "pct_20d", "vol_ratio", "turn_ratio", "dist_high20"]].isna().mean()
    print((miss * 100).round(2).sort_values(ascending=False).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
