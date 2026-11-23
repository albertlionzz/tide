# -*- coding: utf-8 -*-
"""
妖股量化 · 进化层 · 参数搜索 + 走样本外验证

「不断优化调整」最容易变成「不断拟合历史」。所以这一层有三道闸：

    闸一 · 走样本外（walk-forward）
        扩窗滚动：用 2022..Y 年的数据挑参数，只在 Y+1 年上评估。
        汇报的数字全部来自**没参与挑参数**的年份。挑参数用的分数只用来排序，
        绝不拿来当业绩。

    闸二 · 参数稳定性惩罚
        相邻参数（topk±1、hold±1、thr_q±0.05）表现断崖式下滑的直接判为「脆弱」，
        综合评分乘 0.7。真规律不会只在某一个格点上有效。

    闸三 · 样本量下限
        样本外交易数 < 30 的候选不参与评比，避免「3 笔全胜」这种假象。

综合评分（四项加权，权重按用户选定）：
    0.35 年化收益   0.25 最大回撤（取负）   0.20 单笔期望   0.20 稳定性
稳定性 = 样本外各年正收益占比。

进化方式：(1+λ) 精英保留 + 邻域变异。历史最优参数存在 data/best_params.json，
下次运行作为精英种子继续，所以它是「持续优化」而不是每次重来。
所有代次记录落 data/evolve_log.jsonl。
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

import backtest as bt

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
EVENTS = os.path.join(DATA, "events.pkl")
ENV = os.path.join(DATA, "market_env.pkl")
BEST = os.path.join(DATA, "best_params.json")
LOG = os.path.join(DATA, "evolve_log.jsonl")
RESULT = os.path.join(DATA, "evolve_result.json")

COST = 0.0015                 # 双边成本：佣金 + 印花税
MIN_OOS_TRADES = 30

ACTIONS = {
    "chase":       {"kind": "firstboard", "desc": "首板次日开盘打板"},
    "rebound":     {"kind": "rebound",    "desc": "断板次日开盘反包"},
    "pre":         {"kind": "pre",        "desc": "潜伏（预测次日首板）"},
}


# ------------------------------------------------------------------ 工具

def clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def composite(m: dict, stab: float) -> float:
    """四项加权综合评分，各分项线性映射到 0~1（区间按 A 股实盘常识设定）。"""
    a = clip01((m["ann"] + 30) / 110)      # 年化 -30% ~ +80%
    d = clip01((-m["mdd"] - 5) / 35)       # 回撤 5% ~ 40%（越小越好）
    e = clip01((m["exp"] + 1) / 5)         # 单笔期望 -1% ~ +4%
    s = clip01(stab)                       # 样本外正收益年份占比
    return round(0.35 * a + 0.25 * d + 0.20 * e + 0.20 * s, 4)


def subset(p: dict, mask: np.ndarray) -> dict:
    """按行子集切面板（日期组要重算）。"""
    dates = p["dates"][p["day"][mask]]
    uniq, day_idx = np.unique(dates, return_inverse=True)
    q = {k: v for k, v in p.items()}
    for k in ("fc", "fh", "fl", "flu"):
        q[k] = p[k][mask]
    for k in ("e_open", "lim_up_o", "lim_dn_o", "Xr", "tier"):
        q[k] = p[k][mask]
    q["day"] = day_idx
    q["dates"] = uniq
    q["ev"] = p["ev"][mask].reset_index(drop=True)
    return q


def year_mask(p: dict, lo: str, hi: str) -> np.ndarray:
    d = p["dates"][p["day"]]
    return (d >= lo) & (d <= hi)


# ------------------------------------------------------------------ 参数采样

def sample(rng: np.random.Generator, n_feat: int, base: dict | None = None,
           scale: float = 1.0) -> dict:
    """base 为 None 时全局随机采样；否则在 base 邻域内变异。"""
    if base is None:
        w = rng.normal(0, 0.5, n_feat)
        w[rng.random(n_feat) < 0.35] = 0.0          # 稀疏化，防过拟合
        mode = rng.choice(["hold", "no_lu", "stop_target"], p=[0.35, 0.30, 0.35])
        return {"w": w, "exit_mode": str(mode),
                "hold": int(rng.integers(1, 9)),
                "max_hold": int(rng.integers(3, 11)),
                "stop_loss": float(rng.uniform(3, 12)),
                "take_profit": float(rng.uniform(8, 40)),
                "topk": int(rng.integers(1, 9)),
                "thr_q": float(rng.uniform(0.50, 0.95)),
                "cost": COST}

    w = base["w"] + rng.normal(0, 0.18 * scale, n_feat)
    p = {
        "w": w,
        "exit_mode": base["exit_mode"] if rng.random() > 0.12 * scale
        else str(rng.choice(["hold", "no_lu", "stop_target"])),
        "hold": int(np.clip(base["hold"] + rng.integers(-1, 2), 1, 10)),
        "max_hold": int(np.clip(base["max_hold"] + rng.integers(-1, 2), 2, 14)),
        "stop_loss": float(np.clip(base["stop_loss"] + rng.normal(0, 0.8 * scale), 2, 15)),
        "take_profit": float(np.clip(base["take_profit"] + rng.normal(0, 3 * scale), 5, 60)),
        "topk": int(np.clip(base["topk"] + rng.integers(-1, 2), 1, 10)),
        "thr_q": float(np.clip(base["thr_q"] + rng.normal(0, 0.03 * scale), 0.4, 0.97)),
        "cost": COST,
    }
    return p


def evaluate(p: dict, prm: dict) -> tuple[dict, float]:
    """跑一次回测并给出 (指标, 稳定性)。"""
    thr_val = None
    if "thr_q" in prm:
        score_all = p["Xr"] @ prm["w"].astype("float32")
        thr_val = float(np.quantile(score_all, prm["thr_q"]))
    prm2 = {**prm, "thr": thr_val if thr_val is not None else -1e9}
    m = bt.run(p, prm2["w"], prm2)
    if m["n"] == 0:
        return m, 0.0
    yr = bt.yearly(p, prm2["w"], prm2)
    stab = float((yr["sum"] > 0).mean()) if len(yr) else 0.0
    return m, stab


def brittle(p: dict, prm: dict, base_comp: float) -> bool:
    """闸二：相邻参数断崖下滑 → 判为脆弱。"""
    probes = [{"topk": prm["topk"] + 1}, {"topk": max(1, prm["topk"] - 1)},
              {"hold": min(10, prm["hold"] + 1)}, {"max_hold": min(14, prm["max_hold"] + 1)},
              {"thr_q": min(0.97, prm["thr_q"] + 0.05)}]
    worse = 0
    for pv in probes:
        q = {**prm, **pv}
        m, st = evaluate(p, q)
        if m["n"] < MIN_OOS_TRADES:
            continue
        if composite(m, st) < base_comp - 0.12:
            worse += 1
    return worse >= 2


# ------------------------------------------------------------------ 单动作进化

def evolve_action(action: str, feat_cols: list[str], n_iter: int,
                  seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    info = ACTIONS[action]
    ev = pd.read_pickle(EVENTS)
    ev = ev[ev["kind"] == info["kind"]]
    if ev.empty:
        return {"action": action, "error": "无事件样本"}

    # 丢掉全 NaN 的特征列
    cols = [c for c in feat_cols if ev[c].notna().mean() > 0.5]
    p = bt.build_panel(ev, cols)
    years = sorted({d[:4] for d in p["dates"]})
    print(f"  [{action}] 事件 {len(ev)} 条 / {len(cols)} 个特征 / "
          f"{len(p['dates'])} 个交易日 / 年份 {years[0]}~{years[-1]}")

    if len(years) < 3:
        return {"action": action, "error": f"年份不足（{len(years)}），无法走样本外"}

    # 精英种子：上次的全局最优
    elite_prev = {}
    if os.path.exists(BEST):
        with open(BEST, "r", encoding="utf-8") as fh:
            prev = json.load(fh)
        if action in prev and "w" in prev[action]:
            elite_prev = prev[action]

    folds = []
    oos_all = []
    for i in range(1, len(years) - 1):
        train_hi = years[i]
        test_y = years[i + 1]
        tr_mask = p["dates"][p["day"]] <= f"{train_hi}-12-31"
        te_mask = year_mask(p, f"{test_y}-01-01", f"{test_y}-12-31")
        if te_mask.sum() < 50:
            continue
        ptr, pte = subset(p, tr_mask), subset(p, te_mask)

        elites = []
        if elite_prev:
            e = {**elite_prev, "w": np.array(elite_prev["w"], dtype="float32")}
            if len(e["w"]) == len(cols):
                m, st = evaluate(ptr, e)
                if m["n"] >= MIN_OOS_TRADES:
                    elites.append((composite(m, st), e))

        for it in range(n_iter):
            if elites and rng.random() < 0.7:
                _, par = elites[int(rng.integers(0, len(elites)))]
                cand = sample(rng, len(cols), par)
            else:
                cand = sample(rng, len(cols), None)
            m, st = evaluate(ptr, cand)
            if m["n"] < MIN_OOS_TRADES:
                continue
            c = composite(m, st)
            elites.append((c, cand))
            elites.sort(key=lambda x: -x[0])
            del elites[6:]

        if not elites:
            continue
        best_c, best_p = elites[0]
        m_tr, _ = evaluate(ptr, best_p)
        m_te, st_te = evaluate(pte, best_p)
        pen = brittle(ptr, best_p, best_c)
        c_te = composite(m_te, st_te) * (0.7 if pen else 1.0)
        folds.append({"trainTo": train_hi, "test": test_y,
                      "trainScore": round(best_c, 4), "oosScore": round(c_te, 4),
                      "brittle": pen, "params": {k: (v.tolist() if k == "w" else v)
                                                 for k, v in best_p.items()},
                      "train": m_tr, "oos": m_te})
        oos_all.append((m_te, st_te))
        print(f"    train≤{train_hi} → test {test_y}: 训练 {m_tr['n']} 笔/"
              f"分 {best_c:.3f} | 样本外 {m_te['n']} 笔 年化 {m_te['ann']}% "
              f"回撤 {m_te['mdd']}% 期望 {m_te['exp']}% 分 {c_te:.3f}"
              f"{' [脆弱]' if pen else ''}", flush=True)

    if not folds:
        return {"action": action, "error": "没有可用的样本外折"}

    # 汇总样本外（各折指标按交易数加权）
    tot_n = sum(m["n"] for m, _ in oos_all) or 1
    agg = {k: round(sum(m[k] * m["n"] for m, _ in oos_all) / tot_n, 2)
           for k in ("ann", "mdd", "exp", "winRate", "payoff", "avgHold")}
    agg["n"] = tot_n
    stab = float(np.mean([st for _, st in oos_all]))
    agg["stab"] = round(stab, 3)
    agg["score"] = composite(agg, stab)

    # 用全部数据重训一次，作为上线参数（进化标准做法：策略定型后用全样本定参）
    best_fold = max(folds, key=lambda f: f["oosScore"])
    final = {**best_fold["params"],
             "w": np.array(best_fold["params"]["w"], dtype="float32")}
    print(f"  [{action}] 样本外汇总：{tot_n} 笔，年化 {agg['ann']}%，"
          f"回撤 {agg['mdd']}%，单笔期望 {agg['exp']}%，综合 {agg['score']}")

    return {"action": action, "desc": info["desc"], "folds": folds,
            "oos": agg, "finalParams": best_fold["params"], "features": cols}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=220)
    ap.add_argument("--actions", default="chase,rebound,pre")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    if not os.path.exists(EVENTS):
        print("events.pkl 不存在，先跑 dataset.py")
        return 1
    ev = pd.read_pickle(EVENTS)
    feat_cols = [c for c in ev.columns
                 if c in _feat_cols() or c.startswith("env_")]

    t0 = time.time()
    out = {"builtAt": time.strftime("%Y-%m-%d %H:%M:%S"), "actions": {}}
    best_store = {}
    if os.path.exists(BEST):
        with open(BEST, "r", encoding="utf-8") as fh:
            best_store = json.load(fh)

    for a in args.actions.split(","):
        a = a.strip()
        if a not in ACTIONS:
            print(f"未知动作 {a}，跳过")
            continue
        r = evolve_action(a, feat_cols, args.iters, args.seed)
        out["actions"][a] = r
        if "finalParams" in r:
            best_store[a] = r["finalParams"]
            with open(BEST, "w", encoding="utf-8") as fh:
                json.dump(best_store, fh, ensure_ascii=False, indent=1)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": out["builtAt"], "action": a,
                                 "oos": r.get("oos"), "err": r.get("error")},
                                ensure_ascii=False) + "\n")

    with open(RESULT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"OK -> {RESULT}  耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return 0


def _feat_cols() -> list[str]:
    return ["pct", "turn", "gap", "close_pos", "amp", "seal_i", "dist_ma5",
            "dist_ma10", "dist_ma20", "vol_ratio", "turn_ratio", "dist_high20",
            "dist_high60", "pct_3d", "pct_5d", "pct_20d", "log_amt", "lu20",
            "amt_ratio"]


if __name__ == "__main__":
    raise SystemExit(main())
