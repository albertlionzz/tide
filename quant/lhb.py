# -*- coding: utf-8 -*-
"""
妖股量化 · 数据层 · 龙虎榜因子

data/lhb/<YYYYMMDD>.json.gz 是 fetch_lhb.py 抓的逐日榜单，每只票一条汇总：
    code name close pct buy sell net deal reason turn
`net` 是当日龙虎榜净买额（元），`deal` 是当日该股总成交额（元）。
席位级明细（哪家营业部买多少）不在这个接口里，所以这里只能做出「资金净流入强度」
一类的因子，做不出「某游资进场」这类标签。

产出的因子（全部只用当日及之前）：
    lhb_hit      当日是否上龙虎榜
    lhb_netr     净买额 ÷ 当日成交额（-1 ~ +1，衡量主力净流入强度）
    lhb_cnt10    近 10 个交易日上榜次数（关注度）
    lhb_netr10   近 10 个交易日 净买额合计 ÷ 成交额合计（持续净流入）
    lhb_turn     上榜当日换手率

⚠️ 口径注意：`lhb_netr10` 用的是「窗口内求和后再相除」，不是两个滚动均值相减。
   滚动累计额跨日相减会混入窗口滑动量，这一点在潮汐项目的游资模块上踩过。
"""

from __future__ import annotations

import glob
import gzip
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
LHB = os.path.join(DATA, "lhb")
OUT = os.path.join(DATA, "lhb.pkl")


def load_raw() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(os.path.join(LHB, "*.json.gz"))):
        d = os.path.basename(f)[:8]
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                arr = json.load(fh)
        except Exception:                              # noqa: BLE001
            continue
        for r in arr:
            if not r.get("code"):
                continue
            rows.append({"code": r["code"], "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                         "lhb_net": r.get("net") or 0.0,
                         "lhb_deal": r.get("deal") or 0.0,
                         "lhb_turn": r.get("turn"),
                         "lhb_pct": r.get("pct")})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 同一只票同日可能因多条上榜原因重复，按净买额最大的一条保留
    df = (df.sort_values("lhb_net", ascending=False)
            .drop_duplicates(["code", "date"], keep="first")
            .reset_index(drop=True))
    return df


def build(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df["lhb_hit"] = 1
    df["lhb_netr"] = (df["lhb_net"] / df["lhb_deal"].replace(0, np.nan)).round(4)

    g = df.groupby("code", group_keys=False)
    df["lhb_cnt10"] = (g["lhb_hit"].transform(lambda s: s.rolling(10, min_periods=1).sum())
                       .clip(upper=10).astype("int8"))
    # 窗口内先求和再相除（不可用滚动均值相减）
    net10 = g["lhb_net"].transform(lambda s: s.rolling(10, min_periods=1).sum())
    deal10 = g["lhb_deal"].transform(lambda s: s.rolling(10, min_periods=1).sum())
    df["lhb_netr10"] = (net10 / deal10.replace(0, np.nan)).round(4)
    return df[["code", "date", "lhb_hit", "lhb_net", "lhb_deal",
               "lhb_netr", "lhb_cnt10", "lhb_netr10", "lhb_turn"]]


def main() -> int:
    raw = load_raw()
    if raw.empty:
        print("data/lhb 为空，先跑 fetch_lhb.py")
        return 1
    out = build(raw)
    out.to_pickle(OUT)
    print(f"龙虎榜 {len(out):,} 条 / {out['code'].nunique()} 只 / "
          f"{out['date'].min()}~{out['date'].max()}")
    print(out.head(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
