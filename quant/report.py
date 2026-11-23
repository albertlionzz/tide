# -*- coding: utf-8 -*-
"""
妖股量化 · 报告层 · 把「学了什么」讲清楚

输出两份：
    report.md        给人看的完整报告（数据概览 / 妖股画像 / 三动作样本外结果 / 局限）
    data/result.json 给程序看的结构化结果（网站、后续复盘都能直接用）

画像部分的对比逻辑：
    把首板事件按「后续 20 日走成什么」分成 妖股(S/A) / 次级(B) / 未成妖(N) 三组，
    比较它们**在启动当日**可见的特征均值差。
    这个差值就是模型能学到的东西的上限——如果某特征三组几乎无差别，
    那它对「这只首板会不会成妖」没有信息量，不该占权重。
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
EVENTS = os.path.join(DATA, "events.pkl")
ENV = os.path.join(DATA, "market_env.pkl")
RESULT = os.path.join(DATA, "evolve_result.json")
OUT_MD = os.path.join(HERE, "report.md")
OUT_JSON = os.path.join(DATA, "result.json")

PROFILE_FEATS = [
    ("turn", "首板换手率 %"), ("pct", "首板涨幅 %"), ("vol_ratio", "量比（对比 5 日均量）"),
    ("amt_ratio", "成交额比 5 日均"), ("close_pos", "收盘在当日振幅位置"),
    ("seal_i", "是否一字板"), ("dist_high20", "距 20 日高点 %"),
    ("dist_ma20", "距 20 日均线 %"), ("pct_20d", "前 20 日涨幅 %"),
    ("lu20", "近 20 日涨停次数"), ("log_amt", "成交额(log)"),
    ("lhb_hit", "当日上龙虎榜"), ("lhb_cnt10", "近 10 日上榜次数"),
    ("lhb_netr10", "近 10 日净买占比"),
    ("env_lu", "全市场涨停家数"), ("env_ld", "全市场跌停家数"),
    ("env_up_ratio", "上涨家数占比 %"), ("env_lbmax", "最高连板"),
    ("env_cycle", "情绪周期(5日/20日)"),
]


def fmt(v: float, nd: int = 2) -> str:
    return "—" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{nd}f}"


def main() -> int:
    ev = pd.read_pickle(EVENTS)
    env = pd.read_pickle(ENV)
    fb = ev[ev["kind"] == "firstboard"].copy()

    # ---------------- 妖股画像
    grp = {"S/A 妖股": fb[fb["tier"].isin(["S", "A"])],
           "B 次级": fb[fb["tier"] == "B"],
           "N 未成妖": fb[fb["tier"] == "N"]}
    rows = []
    for col, label in PROFILE_FEATS:
        if col not in fb.columns:
            continue
        rows.append([label] + [fmt(g[col].mean()) for g in grp.values()])
    prof = pd.DataFrame(rows, columns=["启动日特征", "S/A 妖股", "B 次级", "N 未成妖"])

    # 分层统计
    tier_stat = []
    for t in ("S", "A", "B", "N"):
        s = fb[fb["tier"] == t]
        tier_stat.append({"tier": t, "n": len(s),
                          "avgMaxLb": round(float(s["mx_lb"].mean()), 2) if len(s) else 0,
                          "avgRet": round(float(s["mx_ret"].mean()), 2) if len(s) else 0})
    # 只看有未来数据的样本
    fb2 = fb[fb["tier"].isin(["S", "A", "B"])]
    year = fb2["date"].str[:4]

    # ---------------- 进化结果
    res = {}
    if os.path.exists(RESULT):
        with open(RESULT, "r", encoding="utf-8") as fh:
            res = json.load(fh)

    md = []
    md.append("# 妖股量化模型 · 研究报告\n")
    md.append(f"生成时间：{res.get('builtAt', '—')}　数据区间："
              f"{env['date'].min()} ~ {env['date'].max()}　"
              f"覆盖 {env['total'].max()} 只（主板+创业板+科创板，不含北交所）\n")

    md.append("## 一、样本概况\n")
    md.append("用日线自行重算涨停与连板（东财历史涨停池只保留 1 个月，不能用于长周期研究）。"
              "判定同时满足三条：涨幅达阈值、涨幅不超过阈值+2 个百分点（排除新股无涨跌幅日）、"
              "收盘等于当日最高价（确认真封板）。\n")
    md.append(f"- 首板事件 **{len(fb)}** 条；断板反包候选 "
              f"{len(ev[ev['kind'] == 'rebound'])} 条；潜伏候选 "
              f"{len(ev[ev['kind'] == 'pre'])} 条")
    md.append(f"- 其中走成妖股（S 级：≥5 连板或 20 日涨 100%）**"
              f"{int((fb['tier'] == 'S').sum())}** 条，A 级 "
              f"{int((fb['tier'] == 'A').sum())} 条，B 级 "
              f"{int((fb['tier'] == 'B').sum())} 条，未成妖 "
              f"{int((fb['tier'] == 'N').sum())} 条")
    rate = (fb["tier"].isin(["S", "A"])).mean() * 100
    md.append(f"- **首板成妖率 {rate:.1f}%** —— 这就是这个问题的难度基线："
              f"随便抓一个首板，{100 - rate:.1f}% 的概率不会走成妖股\n")

    md.append("### 分层统计\n")
    md.append("| 层级 | 样本数 | 平均最大连板 | 平均区间最大涨幅 |")
    md.append("|---|---|---|---|")
    for r in tier_stat:
        md.append(f"| {r['tier']} | {r['n']} | {r['avgMaxLb']} | {r['avgRet']}% |")

    md.append("\n## 二、妖股在启动当天的样子（画像）\n")
    md.append("对比三组首板在**启动当日收盘时**就能看到的特征。差值越大，这个特征越有区分力。\n")
    md.append("| " + " | ".join(prof.columns) + " |")
    md.append("|" + "---|" * len(prof.columns))
    for _, r in prof.iterrows():
        md.append("| " + " | ".join(str(x) for x in r.tolist()) + " |")

    md.append("\n## 三、三种动作的走样本外结果\n")
    md.append("扩窗滚动：用 2022~Y 年数据挑参数，只在 Y+1 年评估。"
              "下表数字全部来自**没有参与调参**的年份。成本按双边 0.15% 计。\n")
    if res.get("actions"):
        md.append("| 动作 | 样本外笔数 | 年化 | 最大回撤 | 单笔期望 | 胜率 | 盈亏比 | 平均持有 | 综合评分 |")
        md.append("|---|---|---|---|---|---|---|---|---|")
        name = {"chase": "首板打板", "rebound": "断板反包", "pre": "启动前潜伏"}
        for k, v in res["actions"].items():
            if "oos" not in v:
                continue
            o = v["oos"]
            md.append(f"| {name.get(k, k)} | {o['n']} | {o['ann']}% | {o['mdd']}% | "
                      f"{o['exp']}% | {o['winRate']}% | {o['payoff']} | "
                      f"{o['avgHold']} 天 | {o['score']} |")

        md.append("\n### 逐折明细\n")
        for k, v in res["actions"].items():
            if "folds" not in v:
                continue
            md.append(f"**{name.get(k, k)}**（{v.get('desc', '')}）\n")
            md.append("| 训练到 | 测试年 | 训练笔数 | 样本外笔数 | 样本外年化 | 样本外回撤 | 样本外期望 | 参数脆弱 |")
            md.append("|---|---|---|---|---|---|---|---|")
            for f in v["folds"]:
                md.append(f"| {f['trainTo']} | {f['test']} | {f['train']['n']} | "
                          f"{f['oos']['n']} | {f['oos']['ann']}% | {f['oos']['mdd']}% | "
                          f"{f['oos']['exp']}% | {'是' if f['brittle'] else '否'} |")
            md.append("")

        md.append("### 最优参数（各动作最后一个折选出的）\n")
        for k, v in res["actions"].items():
            p = v.get("finalParams")
            if not p:
                continue
            mode = {"hold": "固定持有", "no_lu": "断板即走", "stop_target": "止损止盈"}[p["exit_mode"]]
            hold_note = f"（持 {p['hold']} 日）" if p["exit_mode"] == "hold" else ""
            md.append(f"- **{name.get(k, k)}**：出场={mode}{hold_note}"
                      f"，每日最多 {p['topk']} 只，打分阈值分位 {p['thr_q']:.2f}"
                      f"，止损 {p['stop_loss']:.1f}%，止盈 {p['take_profit']:.1f}%，"
                      f"最长持有 {p['max_hold']} 日")
    else:
        md.append("（尚未运行进化层）")

    md.append("\n## 四、这套东西的局限（必须知道）\n")
    md.append("1. **样本量天然稀缺**。S 级妖股只有几百条，任何模型在这种样本量上都容易过拟合。"
              "所以这套系统用线性打分而不是深度模型，参数越少越可信。")
    md.append("2. **买入假设偏乐观**。打板按「次日开盘价」成交，实际竞价能否买到取决于排队位置，"
              "一字板已剔除，但开盘瞬间的滑点没有建模。")
    md.append("3. **卖出假设偏乐观**。跌停日实际上卖不掉，模型按收盘价成交，"
              "真实回撤会更大。成本只按 0.15% 双边计，没有算冲击成本。")
    md.append("4. **换手率是代理值**。新浪日线不含换手率，用「成交量 ÷ 当前流通股本」反推，"
              "历史上有送转/增发的票会偏。")
    md.append("5. **龙虎榜只有 2023 年起**，相关因子在 2022 年样本上是空的。")
    md.append("6. **样本外年化不等于可实现的收益**。它只说明「这套规则在没见过的年份里有没有正期望」，"
              "震荡市与单边下跌市的差异会很大。")

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")

    out = {"builtAt": res.get("builtAt"), "span": [env["date"].min(), env["date"].max()],
           "tierStat": tier_stat, "monsterRate": round(rate, 2),
           "profile": prof.to_dict(orient="records"),
           "actions": res.get("actions", {})}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    print(f"OK -> {OUT_MD}")
    print(f"     首板成妖率 {rate:.1f}%　S 级 {int((fb['tier'] == 'S').sum())} 条　"
          f"报告 {len('\n'.join(md))} 字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
