# -*- coding: utf-8 -*-
"""
潮汐 · 本地台账持续分析
========================
读 `data/daily/*/raw_snapshot.json`（每交易日一份存档），用**当前 derive** 重算，
产出跨日结论 `analysis.json`。

为什么要单独一层：
  `history.json` 只给趋势图喂点，`delta.json` 只比「今天 vs 昨天」。
  真正需要判断的是**连续性**：跌停是否连续压过涨停、成交是缩量还是放量、
  恐慌在本地台账里的位置、板块是普跌还是分化 —— 这些要跨全部存档日算。

红线：
  - 只读本地存档，缺失的交易日**不插值、不猜**；样本少就在 note 里说明。
  - 所有结论都能从 series 里的数字复算出来，不写"感觉"。
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import derive                                    # noqa: E402

DAILY = os.path.join(HERE, "data", "daily")


def load_series():
    # 归档日 + 当日根目录快照（当日尚未归档时也要算进来，否则分析永远停在昨天）
    paths = sorted(glob.glob(os.path.join(DAILY, "*", "raw_snapshot.json")))
    cur = os.path.join(HERE, "raw_snapshot.json")
    if os.path.exists(cur):
        paths.append(cur)
    rows, seen = [], set()
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            d = derive.build(raw)
        except Exception as e:                   # noqa: BLE001
            print(f"  [!] 跳过 {p}: {e}")
            continue
        if raw["tradeDate"] in seen:
            continue
        seen.add(raw["tradeDate"])
        idx = {i["name"]: i for i in d["indices"]}
        sh = idx.get("上证指数") or {}
        bs = d.get("boardStats") or {}
        rows.append({
            "date": raw["tradeDate"],
            "fear": d["fear"]["value"],
            "label": d["fear"]["label"],
            "sh": sh.get("price"),
            "shPct": sh.get("pct"),
            "up": d["limit"]["upTotal"],
            "down": d["limit"]["downTotal"],
            "maxBoards": d["limit"]["maxBoards"],
            "adv": d["breadth"]["advancers"],
            "dec": d["breadth"]["decliners"],
            "turnoverYi": round(d["turnoverYi"], 0),
            "boardUp": bs.get("up"),
            "boardTotal": bs.get("total"),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def pct_rank(vals, v):
    """v 在 vals 中的分位（0~100，越大越高）。样本少，只用于粗定位。"""
    if not vals:
        return None
    less = sum(1 for x in vals if x < v)
    equal = sum(1 for x in vals if x == v)
    return round((less + 0.5 * equal) / len(vals) * 100, 0)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def main():
    rows = load_series()
    if len(rows) < 2:
        out = {"available": False, "note": "本地台账不足 2 个交易日，跳过跨日分析"}
        with open(os.path.join(HERE, "analysis.json"), "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("本地台账不足 2 个交易日，已写空 analysis.json")
        return 0

    cur = rows[-1]
    prev = rows[-2]
    fears = [r["fear"] for r in rows]
    turns = [r["turnoverYi"] for r in rows]
    adv_ratios = [round(r["adv"] / (r["adv"] + r["dec"]) * 100, 1)
                  if (r["adv"] + r["dec"]) else None for r in rows]

    findings = []

    # ① 恐慌在本地台账中的位置
    pr = pct_rank(fears, cur["fear"])
    if pr is not None:
        hi = max(rows, key=lambda r: r["fear"])
        lo = min(rows, key=lambda r: r["fear"])
        findings.append({
            "tone": "down" if cur["fear"] >= 70 else "info",
            "text": f"恐慌 {cur['fear']}（{cur['label']}），在本地 {len(rows)} 个交易日里处于第 {int(pr)} 分位；"
                    f"区间最高 {hi['fear']}（{hi['date']}）、最低 {lo['fear']}（{lo['date']}）。",
        })

    # ② 跌停压过涨停的连续天数（风险释放的持续性）
    streak = 0
    for r in reversed(rows):
        if r["down"] > r["up"]:
            streak += 1
        else:
            break
    if streak:
        findings.append({
            "tone": "down",
            "text": f"跌停已连续 {streak} 个交易日多于涨停（最近：跌停 {cur['down']} / 涨停 {cur['up']}），"
                    f"亏钱效应仍在扩散。",
        })
    else:
        # 今日已翻正，往回数「上一段」连续跌停占优的天数（跳过今天，否则恒为 0）
        back = 0
        for r in reversed(rows[:-1]):
            if r["down"] > r["up"]:
                back += 1
            else:
                break
        findings.append({
            "tone": "up",
            "text": f"涨停 {cur['up']} 家重新多于跌停 {cur['down']} 家，"
                    f"此前连续 {back} 日跌停占优的格局被打断，风险释放出现停顿。",
        })

    # ③ 成交额：近 3 日 vs 前 3 日
    if len(turns) >= 4:
        last3 = mean(turns[-3:])
        prev3 = mean(turns[-6:-3]) if len(turns) >= 6 else mean(turns[:-3])
        if last3 and prev3:
            chg = round((last3 / prev3 - 1) * 100, 1)
            findings.append({
                "tone": "info",
                "text": f"近 3 日日均成交 {last3:.0f} 亿，较前段 {prev3:.0f} 亿 {'放量' if chg > 0 else '缩量'} {abs(chg):.1f}%；"
                        f"今日 {cur['turnoverYi']:.0f} 亿。",
            })

    # ④ 上涨家数占比：判断普跌还是结构性
    ar = adv_ratios[-1]
    if ar is not None:
        tone = "down" if ar < 30 else ("up" if ar > 60 else "info")
        findings.append({
            "tone": tone,
            "text": f"上涨家数占比 {ar:.1f}%（{cur['adv']} 涨 / {cur['dec']} 跌），"
                    f"{'全面普跌' if ar < 25 else '跌多涨少' if ar < 45 else '多空接近' if ar < 60 else '普涨'}；"
                    f"本地台账最低 {min(x for x in adv_ratios if x is not None):.1f}%、"
                    f"最高 {max(x for x in adv_ratios if x is not None):.1f}%。",
        })

    # ⑤ 板块宽度（只在有全样本口径的日期之间可比）
    if cur.get("boardTotal") and cur.get("boardUp") is not None:
        same = [r for r in rows if r.get("boardTotal") == cur["boardTotal"]]
        ratio = round(cur["boardUp"] / cur["boardTotal"] * 100, 1)
        findings.append({
            "tone": "down" if ratio < 30 else "info",
            "text": f"行业宽度：{cur['boardUp']} / {cur['boardTotal']} 个行业上涨（{ratio}%），"
                    f"可与同为全样本口径的 {len(same)} 个交易日横向比。",
        })

    # ⑥ 连板空间
    if cur["maxBoards"] != prev["maxBoards"]:
        findings.append({
            "tone": "up" if cur["maxBoards"] > prev["maxBoards"] else "down",
            "text": f"最高连板 {prev['maxBoards']} → {cur['maxBoards']} 板，"
                    f"{'短线资金愿意打高' if cur['maxBoards'] > prev['maxBoards'] else '高度被压缩，接力意愿弱'}。",
        })

    # ⑦ 恐慌的边际变化方向（连续同向）
    dirs = []
    for a, b in zip(rows, rows[1:]):
        dirs.append(b["fear"] - a["fear"])
    if dirs:
        last_dir = 1 if dirs[-1] > 0 else (-1 if dirs[-1] < 0 else 0)
        run = 0
        for x in reversed(dirs):
            if (1 if x > 0 else (-1 if x < 0 else 0)) == last_dir and last_dir != 0:
                run += 1
            else:
                break
        if run >= 2:
            def _ar(r):
                t = (r.get("adv") or 0) + (r.get("dec") or 0)
                return (r["adv"] / t * 100) if t else None

            prev_ar, cur_ar = _ar(prev), _ar(cur)
            if last_dir > 0:
                findings.append({
                    "tone": "down",
                    "text": f"恐慌已连续 {run} 个记录日抬升，最新一次 {prev['fear']} → {cur['fear']}。",
                })
            elif prev_ar is not None and cur_ar is not None and cur_ar < prev_ar - 1:
                # 恐慌降而宽度也降 —— 指数跌幅小掩盖了普跌，不能算情绪修复
                findings.append({
                    "tone": "down",
                    "text": f"恐慌自 {prev['fear']} 回落至 {cur['fear']}（连续 {run} 个记录日），"
                            f"但上涨家数占比同步走低 {prev_ar:.1f}% → {cur_ar:.1f}%，"
                            f"指数跌幅小掩盖了普跌，不构成情绪修复。",
                })
            else:
                findings.append({
                    "tone": "up",
                    "text": f"恐慌已连续 {run} 个记录日回落，最新一次 {prev['fear']} → {cur['fear']}，"
                            f"且上涨家数未继续恶化。",
                })

    out = {
        "available": True,
        "asOf": cur["date"],
        "days": len(rows),
        "range": [rows[0]["date"], cur["date"]],
        "series": rows,
        "stats": {
            "fear": {"min": min(fears), "max": max(fears), "mean": mean(fears),
                     "pctRank": pr},
            "advRatio": {"last": ar,
                         "min": min(x for x in adv_ratios if x is not None),
                         "max": max(x for x in adv_ratios if x is not None)},
            "turnover": {"last": cur["turnoverYi"], "ma3": mean(turns[-3:]),
                         "min": min(turns), "max": max(turns)},
            "downStreak": streak,
        },
        "findings": findings,
        "note": (f"本地台账 {len(rows)} 个交易日（{rows[0]['date']} ~ {cur['date']}），"
                 f"全部用当前 derive 公式重算，缺日不插值；样本稀疏，分位数仅作粗定位。"
                 f"归档在 data/daily/，每天新增一份，可回溯重算。"),
    }
    with open(os.path.join(HERE, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"analysis.json 写出（{len(rows)} 个交易日 · {out['range'][0]} ~ {out['range'][1]}）")
    for f in findings:
        print(f"  [{f['tone']}] {f['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
