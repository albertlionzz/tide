#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""潮汐 Tide — 数据加工管道。

输入: raw_snapshot.json  (通达信 MCP 实抓的原始行情)
输出: tide_data.json     (前端直接消费的数据载荷, 含合成恐慌指数)

恐慌指数（0-100）五分量合成，全部来自当日真实数据：
  M1 涨跌家数失衡   —— 全市场跌家数占比（涨=风险偏好高，跌=恐慌高）
  M2 跌停扩散度     —— 跌停家数 / (涨停家数+跌停家数)，衡量杀跌强度
  M3 连板高度       —— 最高连板数，高度越高=情绪越亢奋=恐慌越低
  M4 赚钱效应       —— 涨停池一字板占比 + 封单强度（封得死=承接好）
  M5 指数振幅       —— 三大指数平均振幅，波动放大=恐慌上升

所有分量统一映射到「恐慌方向」（越高越恐慌），再加权。
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw_snapshot.json"
OUT = HERE / "tide_data.json"

# 分量权重（合计 1.0）——涨跌家数权重最高，因为它是市场宽度最直接的读数
WEIGHTS = {
    "breadth": 0.30,   # M1 涨跌家数失衡
    "limitdown": 0.22,  # M2 跌停扩散度
    "height": 0.18,   # M3 连板高度（反向）
    "money": 0.15,   # M4 赚钱效应（反向）
    "amplitude": 0.15,  # M5 指数振幅
}

BANDS = [
    (0, 20, "冷静", "波动收缩，可逢低布局"),
    (21, 40, "平稳", "正常轮动，按计划执行"),
    (41, 60, "紧张", "分歧加大，降低追高"),
    (61, 80, "偏高", "避险升温，控制仓位"),
    (81, 100, "恐慌", "极端情绪，逆向观察机会"),
]


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def scale(v: float, lo: float, hi: float, out_lo: float = 0.0, out_hi: float = 100.0) -> float:
    """把 v 从 [lo,hi] 线性映射到 [out_lo,out_hi]，越界截断。

    注意：out_lo > out_hi 时为反向映射（如连板高度：板数越高恐慌越低）。
    截断范围必须按 [min(out_lo,out_hi), max(out_lo,out_hi)] 取——
    早期版本把 out_lo/out_hi 原序传给 clamp，反向映射会被钉死在 out_lo
    （clamp(62.5, 100, 0) = max(100, min(0, 62.5)) = 100），已修复。
    """
    if hi == lo:
        return out_lo
    t = (v - lo) / (hi - lo)
    raw = out_lo + t * (out_hi - out_lo)
    return clamp(raw, min(out_lo, out_hi), max(out_lo, out_hi))


def band_of(v: float) -> tuple[str, str]:
    for lo, hi, name, desc in BANDS:
        if lo <= round(v) <= hi:
            return name, desc
    return "恐慌", "极端情绪，逆向观察机会"


def build(raw: dict) -> dict:
    indices = raw["indices"]
    up_pool = raw["limitUpPool"]
    down_pool = raw["limitDownPool"]

    # ---- 全市场涨跌家数（沪 + 深，指数自带的成分家数）----
    adv = sum(i.get("advancers", 0) for i in indices if i["code"] in ("000001", "399001"))
    dec = sum(i.get("decliners", 0) for i in indices if i["code"] in ("000001", "399001"))
    flat = sum(i.get("unchanged", 0) for i in indices if i["code"] in ("000001", "399001"))
    total = adv + dec + flat
    breadth_ratio = dec / total if total else 0.5

    # ---- 涨停 / 跌停 ----
    lu_total = raw["limitUpTotal"]          # 全市场涨停家数（筛选器给出）
    ld_total = raw["limitDownTotal"]
    lu_shown = len(up_pool)
    ld_shown = len(down_pool)

    # 跌停扩散度：跌停家数占涨跌停总数的比例，并放大
    limit_total = lu_total + ld_total
    limitdown_share = ld_total / limit_total if limit_total else 0.0

    # ---- 连板高度 ----
    max_boards = max((r["boards"] for r in up_pool), default=1)
    # 高度板（>=3 板）家数，衡量情绪延续性
    high_boards = sum(1 for r in up_pool if r["boards"] >= 3)

    # ---- 赚钱效应：一字板占比 + 平均封单 ----
    sealed = sum(1 for r in up_pool if "一字" in r.get("boardType", ""))
    sealed_ratio = sealed / lu_shown if lu_shown else 0.0
    avg_seal = sum(r["sealAmount"] for r in up_pool) / lu_shown if lu_shown else 0.0
    avg_seal_yi = avg_seal / 1e8

    # ---- 开板次数合计（样本内），用于估算炸板率 ----
    open_times_shown = sum(int(r.get("openTimes") or 0) for r in up_pool)

    # ---- 指数振幅 ----
    amps = []
    for i in indices:
        pc = i.get("prevClose") or i.get("close") or 0
        if pc:
            amps.append((i["high"] - i["low"]) / pc * 100)
    avg_amp = sum(amps) / len(amps) if amps else 0.0

    # ==================== 分量计算（统一朝向「越高越恐慌」） ====================
    # M1 涨跌家数失衡：跌家占比 30%→0 分, 55%→100 分
    m_breadth = scale(breadth_ratio * 100, 30, 55)

    # M2 跌停扩散度：0%→0, 25%→100
    m_limitdown = scale(limitdown_share * 100, 0, 25)

    # M3 连板高度（反向）：9 板→0 分（极亢奋）, 1 板→100 分（无高度=弱）
    m_height = scale(max_boards, 1, 9, 100, 0)
    # 若最高板 >=3 但家数极少，说明只有独苗，恐慌略上修
    if max_boards >= 3 and high_boards <= 2:
        m_height = clamp(m_height + 8)

    # M4 赚钱效应（反向）：一字板占比 0→70 分, 40%→0 分；叠加平均封单
    m_money = scale(sealed_ratio * 100, 0, 40, 70, 0)
    # 平均封单 <2 亿说明封不住，上修恐慌
    if avg_seal_yi < 2:
        m_money = clamp(m_money + 12)
    elif avg_seal_yi > 6:
        m_money = clamp(m_money - 10)

    # M5 指数振幅：0.8%→0 分, 3.5%→100 分
    m_amplitude = scale(avg_amp, 0.8, 3.5)

    parts = {
        "breadth": m_breadth,
        "limitdown": m_limitdown,
        "height": m_height,
        "money": m_money,
        "amplitude": m_amplitude,
    }

    fear = sum(parts[k] * WEIGHTS[k] for k in WEIGHTS)
    fear = clamp(fear)
    fear_rounded = int(round(fear))
    label, desc = band_of(fear)

    # ==================== 指数卡片 ====================
    idx_cards = []
    for i in indices:
        pc = i.get("prevClose") or i.get("close")
        now = i["now"]
        chg = now - pc
        pct = chg / pc * 100 if pc else 0.0
        idx_cards.append({
            "name": i["name"],
            "code": i["code"],
            "price": round(now, 2),
            "change": round(chg, 2),
            "pct": round(pct, 2),
            "amount": i["amount"],
        })

    total_amount = sum(i["amount"] for i in indices if i["code"] in ("000001", "399001"))
    total_amount_yi = total_amount / 1e8  # 亿元

    # ==================== 板块（补算分化度） ====================
    boards = sorted(raw["industryBoards"], key=lambda b: b["pct"], reverse=True)
    board_pcts = [b["pct"] for b in boards]
    board_spread = (max(board_pcts) - min(board_pcts)) if board_pcts else 0.0
    board_up = sum(1 for b in boards if b["pct"] > 0)
    board_down = sum(1 for b in boards if b["pct"] < 0)

    # ==================== 龙虎榜席位（按净买入排序，标注性质） ====================
    seats = []
    for s in raw["seatActivity"]:
        name = s["seat"]
        if name == "机构专用":
            kind = "机构"
        elif "股通" in name:
            kind = "北向"
        else:
            kind = "营业部"
        seats.append({
            "seat": name,
            "kind": kind,
            "lists": s["lists"],
            "buy": round(s["buyAmount"] / 1e4, 2),      # 亿
            "sell": round(s["sellAmount"] / 1e4, 2),    # 亿
            "net": round(s["net"] / 1e4, 2),            # 亿
            "turnover": round(s["turnover"] / 1e4, 2),  # 亿
        })
    seats.sort(key=lambda x: x["net"], reverse=True)

    # ==================== 游资风格标注（基于席位性质 + 上榜频次） ====================
    for s in seats:
        if s["kind"] == "机构":
            s["signal"] = "机构主买" if s["net"] > 0 else "机构撤退"
        elif s["kind"] == "北向":
            s["signal"] = "北向加仓" if s["net"] > 0 else "北向流出"
        elif s["net"] > 0 and s["net"] / max(s["turnover"], 1e-9) > 0.25:
            s["signal"] = "游资净买"
        elif s["net"] > 0:
            s["signal"] = "席位博弈"
        else:
            s["signal"] = "席位出货"

    return {
        "meta": {
            "tradeDate": raw["tradeDate"],
            "capturedAt": raw["capturedAt"],
            "source": raw["source"],
            "generatedBy": "derive.py",
        },
        "fear": {
            "value": fear_rounded,
            "label": label,
            "caption": desc,
            "parts": [
                {"name": "涨跌家数失衡", "value": int(round(m_breadth)), "weight": WEIGHTS["breadth"]},
                {"name": "跌停扩散度", "value": int(round(m_limitdown)), "weight": WEIGHTS["limitdown"]},
                {"name": "连板高度", "value": int(round(m_height)), "weight": WEIGHTS["height"]},
                {"name": "赚钱效应", "value": int(round(m_money)), "weight": WEIGHTS["money"]},
                {"name": "指数振幅", "value": int(round(m_amplitude)), "weight": WEIGHTS["amplitude"]},
            ],
        },
        "breadth": {
            "advancers": adv,
            "decliners": dec,
            "unchanged": flat,
            "total": total,
            "advRatio": round(adv / total * 100, 2) if total else 0,
        },
        "limit": {
            "upTotal": lu_total,
            "downTotal": ld_total,
            "upShown": lu_shown,
            "downShown": ld_shown,
            "maxBoards": max_boards,
            "highBoardCount": high_boards,
            "sealedRatio": round(sealed_ratio * 100, 1),
            "avgSealYi": round(avg_seal_yi, 2),
            # 样本内开板次数合计，用于估算炸板率（开板数 / 样本家数）
            "openTimesShown": open_times_shown,
        },
        "amplitude": round(avg_amp, 2),
        "indices": idx_cards,
        "turnoverYi": round(total_amount_yi, 2),
        "boards": boards,
        "boardStats": {
            "up": board_up,
            "down": board_down,
            "spread": round(board_spread, 2),
        },
        "limitUpPool": up_pool,
        "limitDownPool": down_pool,
        "seats": seats,
        "stockActivity": raw["stockListActivity"],
        "watchlist": raw["watchlist"],
    }


def main() -> None:
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    data = build(raw)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    f = data["fear"]
    print(f"交易日 {data['meta']['tradeDate']}")
    print(f"恐慌指数 {f['value']} ({f['label']}) — {f['caption']}")
    for p in f["parts"]:
        print(f"  {p['name']:<12} {p['value']:>3}  权重 {p['weight']:.2f}")
    b = data["breadth"]
    print(f"涨跌家数 {b['advancers']} 涨 / {b['decliners']} 跌 / {b['unchanged']} 平")
    print(f"涨停 {data['limit']['upTotal']} 家 (最高 {data['limit']['maxBoards']} 板) | "
          f"跌停 {data['limit']['downTotal']} 家 | 一字板占比 {data['limit']['sealedRatio']}%")
    print(f"两市成交 {data['turnoverYi']:.0f} 亿 | 平均振幅 {data['amplitude']}%")
    print(f"板块 涨{data['boardStats']['up']}/跌{data['boardStats']['down']} | "
          f"分化 {data['boardStats']['spread']}pct | 领涨 {data['boards'][0]['name']} "
          f"{data['boards'][0]['pct']:+.2f}%")
    print(f"输出 -> {OUT.name}")


if __name__ == "__main__":
    main()
