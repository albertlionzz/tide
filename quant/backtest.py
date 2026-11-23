# -*- coding: utf-8 -*-
"""
妖股量化 · 回测层 · 向量化事件回测

为什么不是逐笔逐日模拟：
    进化层要跑几千次回测。如果每次都在 Python 里遍历 15 万条事件 × 20 日路径，
    一轮就要几秒，几千轮跑不完。所以把能预先算的一次算清：
        ① 每个事件的未来 20 日路径（dataset 已存）
        ② 出场收益只取决于「出场规则参数」，与选股权重无关
           → hold 模式直接取 f_c[:,h-1]；止损止盈模式用布尔矩阵找首次触发日
        ③ 横截面特征 rank 只跟特征值有关
           → 预计算 (N, F) 的按日百分位矩阵
    每次回测于是只剩「打分 → 排序 → 取前 K → 汇总指标」，毫秒级。

成交假设（全部显式，不许含糊）：
    买入  事件日次日开盘价。若次日一字涨停（lim_up_o）→ **买不进，弃单**，
          弃单率单独统计并计入报告。这是打板战法最容易自欺的地方。
    卖出  按规则在收盘价成交。跌停日卖不出的情形不建模（会略微高估收益），
          另用 cost 参数吸收部分摩擦。
    成本  双边 cost（默认 0.15%：佣金 + 印花税），从单笔收益里扣。

组合曲线：
    每笔固定仓位 1/M（M = 历史最大同时持仓数），按平仓日归集当日损益，
    equity = cumprod(1 + 当日损益)。这是「固定分数下注」的标准近似，
    不建模资金调度与复利加仓，因此回撤数字偏保守，年化偏中性。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

H = 20


def build_panel(ev: pd.DataFrame, feat_cols: list[str]) -> dict:
    """把事件表转成回测用的矩阵面板（只做一次，缓存在内存）。"""
    ev = ev.sort_values(["date", "code"]).reset_index(drop=True)
    dates = ev["date"].to_numpy()
    uniq, day_idx = np.unique(dates, return_inverse=True)

    fc = np.array(ev["f_c"].tolist(), dtype="float32")
    fh = np.array(ev["f_h"].tolist(), dtype="float32")
    fl = np.array(ev["f_l"].tolist(), dtype="float32")
    flu = np.array(ev["f_lu"].tolist(), dtype="int8")

    X = ev[feat_cols].to_numpy(dtype="float32")
    # 按日横截面百分位（NaN 记 0.5，即中性）
    Xr = np.full(X.shape, 0.5, dtype="float32")
    for j in range(X.shape[1]):
        s = pd.Series(X[:, j])
        Xr[:, j] = s.groupby(day_idx).rank(pct=True).fillna(0.5).to_numpy(dtype="float32")

    return {
        "ev": ev, "dates": uniq, "day": day_idx,
        "e_open": ev["e_open"].to_numpy(dtype="float32"),
        "lim_up_o": ev["lim_up_o"].to_numpy(dtype="int8"),
        "lim_dn_o": ev["lim_dn_o"].to_numpy(dtype="int8"),
        "fc": fc, "fh": fh, "fl": fl, "flu": flu,
        "Xr": Xr, "feat": feat_cols,
        "tier": ev["tier"].to_numpy(),
    }


# ------------------------------------------------------------------ 出场规则

def exit_returns(p: dict, prm: dict) -> tuple[np.ndarray, np.ndarray]:
    """返回 (单笔毛收益, 持有天数)。NaN = 路径不足，样本作废。"""
    mode = prm["exit_mode"]
    fc, fh, fl, flu = p["fc"], p["fh"], p["fl"], p["flu"]
    n = fc.shape[0]

    if mode == "hold":
        h = int(prm["hold"])
        ret = fc[:, h - 1].copy()
        days = np.full(n, h, dtype="int16")
        return ret, days

    if mode == "no_lu":
        # 次日未涨停即走；连续涨停则顺延，最长 max_hold 日
        maxh = int(prm["max_hold"])
        hold_ok = flu[:, :maxh] == 1
        # 第一个「未涨停」的位置
        first_no = np.argmax(~hold_ok, axis=1)
        all_ok = hold_ok.all(axis=1)
        k = np.where(all_ok, maxh - 1, first_no)
        ret = fc[np.arange(n), k]
        return ret, (k + 1).astype("int16")

    if mode == "stop_target":
        sl = -abs(float(prm["stop_loss"])) / 100.0
        tp = abs(float(prm["take_profit"])) / 100.0
        maxh = int(prm["max_hold"])
        sub_fc, sub_fl, sub_fh = fc[:, :maxh], fl[:, :maxh], fh[:, :maxh]
        hit_sl = sub_fl <= sl
        hit_tp = sub_fh >= tp
        any_sl = hit_sl.any(axis=1)
        any_tp = hit_tp.any(axis=1)
        k_sl = np.argmax(hit_sl, axis=1)
        k_tp = np.argmax(hit_tp, axis=1)
        # 同一日两者都触发时先判止损（保守）
        use_sl = any_sl & (~any_tp | (k_sl <= k_tp))
        k = np.where(use_sl, k_sl, np.where(any_tp, k_tp, maxh - 1))
        ret = sub_fc[np.arange(n), k]
        # 触发止损/止盈时按触发价成交（限价假设），比收盘价更贴近实盘
        ret = np.where(use_sl, np.maximum(sl, sub_fl[np.arange(n), k]), ret)
        ret = np.where(~use_sl & any_tp, np.minimum(tp, sub_fh[np.arange(n), k]), ret)
        return ret, (k + 1).astype("int16")

    raise ValueError(f"未知 exit_mode: {mode}")


# ------------------------------------------------------------------ 选股

def select(p: dict, w: np.ndarray, prm: dict) -> np.ndarray:
    """按线性打分选股，返回被选中事件的布尔掩码。"""
    score = p["Xr"] @ w.astype("float32")
    keep = score >= float(prm["thr"])
    keep &= (p["lim_up_o"] == 0)          # 一字涨停买不进
    keep &= (p["lim_dn_o"] == 0)          # 一字跌停不买
    if not keep.any():
        return keep
    topk = int(prm["topk"])
    # 按 (交易日, -分数) 排序，取每日前 K
    order = np.lexsort((-score, p["day"]))
    day_sorted = p["day"][order]
    start = np.searchsorted(day_sorted, day_sorted, side="left")
    rank_in_day = np.arange(len(order)) - start
    take = np.zeros(len(order), dtype=bool)
    take[order] = (rank_in_day < topk)
    return keep & take


# ------------------------------------------------------------------ 指标

def metrics(p: dict, ret_gross: np.ndarray, days: np.ndarray,
            mask: np.ndarray, cost: float, m_slots: int = 0) -> dict:
    idx = np.flatnonzero(mask & np.isfinite(ret_gross))
    n_sel = int(mask.sum())
    if len(idx) == 0:
        return {"n": 0, "nSel": n_sel, "winRate": 0, "avgRet": 0, "exp": 0,
                "payoff": 0, "total": 0, "ann": 0, "mdd": 0, "calmar": 0,
                "avgHold": 0, "slots": 0, "giveUp": float(p["lim_up_o"].mean())}

    r = ret_gross[idx] - cost
    d = days[idx]
    day_exit = p["day"][idx] + d
    n_days = len(p["dates"])

    slots = m_slots
    if slots <= 0:
        diff = np.zeros(n_days + H + 2, dtype="int32")
        np.add.at(diff, p["day"][idx], 1)
        np.add.at(diff, np.clip(day_exit, 0, n_days + H + 1), -1)
        slots = int(max(1, np.cumsum(diff[:n_days]).max()))

    pnl = np.zeros(n_days + 1, dtype="float64")
    np.add.at(pnl, np.clip(day_exit, 0, n_days), r / slots)
    eq = np.cumprod(1.0 + pnl)
    total = float(eq[-1] - 1)
    years = max(n_days / 244.0, 1e-9)
    ann = float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min())

    wins = r[r > 0]
    loss = r[r <= 0]
    return {
        "n": int(len(idx)), "nSel": n_sel,
        "winRate": round(float(len(wins) / len(r)) * 100, 2),
        "avgRet": round(float(r.mean()) * 100, 2),
        "exp": round(float(r.mean()) * 100, 2),
        "payoff": round(float(wins.mean() / -loss.mean()), 2)
                  if len(wins) and len(loss) and loss.mean() < 0 else 0.0,
        "total": round(total * 100, 2),
        "ann": round(ann * 100, 2),
        "mdd": round(mdd * 100, 2),
        "calmar": round(ann / abs(mdd), 2) if mdd < 0 else 0.0,
        "avgHold": round(float(d.mean()), 1),
        "slots": slots,
        "giveUp": round(float(p["lim_up_o"].mean()) * 100, 2),
    }


def run(p: dict, w: np.ndarray, prm: dict) -> dict:
    ret, days = exit_returns(p, prm)
    mask = select(p, w, prm)
    return metrics(p, ret, days, mask, float(prm.get("cost", 0.0015)))


def yearly(p: dict, w: np.ndarray, prm: dict) -> pd.DataFrame:
    """逐年表现，用于判断策略是不是只在某一年有效。"""
    ret, days = exit_returns(p, prm)
    mask = select(p, w, prm) & np.isfinite(ret)
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return pd.DataFrame()
    yr = np.array([p["dates"][p["day"][i]][:4] for i in idx])
    r = ret[idx] - float(prm.get("cost", 0.0015))
    df = pd.DataFrame({"year": yr, "ret": r})
    g = df.groupby("year")["ret"]
    out = pd.DataFrame({"n": g.size(), "avg": (g.mean() * 100).round(2),
                        "win": (g.apply(lambda s: (s > 0).mean() * 100)).round(1),
                        "sum": (g.sum() * 100).round(1)})
    return out
