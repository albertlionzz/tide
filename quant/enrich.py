# -*- coding: utf-8 -*-
"""
妖股量化 · 样本层 · 把龙虎榜因子并入事件表

为什么单独一步而不是塞进 dataset：
    龙虎榜只有 2023 年起（939 个交易日），日线覆盖 2022-08 起，两者覆盖期不同。
    先在各自域内算好，再按 (code, date) 对齐，比在 dataset 的双重循环里查表快几十倍，
    也更容易单独复核。

做法：
    全市场交易日铺成日期轴，把「每只票每日净买额 / 成交额 / 上榜次数」铺成矩阵，
    用累积和做 10 日滚动窗口，再按事件行的 (code, date) 取值。

⚠️ 窗口内必须先求和再相除。用两个滚动均值相减会混入窗口滑动量，
   这一点在潮汐项目的游资模块上踩过，结论会整体偏移。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
EVENTS = os.path.join(DATA, "events.pkl")
ENV = os.path.join(DATA, "market_env.pkl")
LHB = os.path.join(DATA, "lhb.pkl")

WIN = 10


def win_sum(a: np.ndarray, w: int) -> np.ndarray:
    """沿日期轴求 w 日滚动和：累积和相减。"""
    cs = np.cumsum(a, axis=1)
    out = cs.copy()
    out[:, w:] = cs[:, w:] - cs[:, :-w]
    return out


def main() -> int:
    for f in (EVENTS, ENV, LHB):
        if not os.path.exists(f):
            print(f"缺少 {os.path.basename(f)}")
            return 1
    ev = pd.read_pickle(EVENTS)
    env = pd.read_pickle(ENV)
    lhb = pd.read_pickle(LHB)

    dates = env["date"].tolist()
    dpos = {d: i for i, d in enumerate(dates)}
    codes = sorted(ev["code"].unique().tolist())
    cpos = {c: i for i, c in enumerate(codes)}
    nc, nd = len(codes), len(dates)

    hit = np.zeros((nc, nd), dtype="float32")
    net = np.zeros((nc, nd), dtype="float64")
    deal = np.zeros((nc, nd), dtype="float64")

    m = lhb[lhb["date"].isin(dpos) & lhb["code"].isin(cpos)]
    ci = m["code"].map(cpos).to_numpy()
    di = m["date"].map(dpos).to_numpy()
    np.add.at(hit, (ci, di), 1.0)
    np.add.at(net, (ci, di), m["lhb_net"].to_numpy(dtype="float64"))
    np.add.at(deal, (ci, di), m["lhb_deal"].to_numpy(dtype="float64"))
    np.clip(hit, 0, 1, out=hit)

    cnt10 = win_sum(hit, WIN)
    net10 = win_sum(net, WIN)
    deal10 = win_sum(deal, WIN)
    with np.errstate(divide="ignore", invalid="ignore"):
        netr = np.where(deal > 0, net / np.where(deal == 0, np.nan, deal), 0.0)
        netr10 = np.where(deal10 > 0, net10 / np.where(deal10 == 0, np.nan, deal10), 0.0)

    ei = ev["code"].map(cpos).to_numpy()
    ej = ev["date"].map(dpos).to_numpy()
    ok = pd.notna(ei) & pd.notna(ej)
    ei = np.where(ok, ei, 0).astype(int)
    ej = np.where(ok, ej, 0).astype(int)

    ev["lhb_hit"] = np.where(ok, hit[ei, ej], np.nan).astype("float32")
    ev["lhb_netr"] = np.where(ok, netr[ei, ej], np.nan).astype("float32")
    ev["lhb_cnt10"] = np.where(ok, cnt10[ei, ej], np.nan).astype("float32")
    ev["lhb_netr10"] = np.where(ok, netr10[ei, ej], np.nan).astype("float32")

    ev.to_pickle(EVENTS)
    sub = ev[(ev["kind"] == "firstboard") & ev["lhb_hit"].notna()]
    print(f"已并入龙虎榜因子：{len(ev)} 条事件（可对齐 {int(ok.sum())} 条）")
    print(f"  首板样本当日上榜占比 {sub['lhb_hit'].mean() * 100:.1f}%  "
          f"近 10 日上榜均值 {sub['lhb_cnt10'].mean():.2f} 次  "
          f"10 日净买占比均值 {sub['lhb_netr10'].mean():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
