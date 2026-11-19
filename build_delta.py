# -*- coding: utf-8 -*-
"""
潮汐 · 逐日变动分析（day-over-day）
====================================
只回答一个问题：**和上一个交易日比，什么变了、变成了什么方向。**

为什么单独成脚本：
  · 页面上的趋势图只画了少数几个总量指标，看不出「结构变了没有」——
    比如恐慌从 83 到 79 是缓和，但跌停从 67 家掉到 40 家、机构从净卖转净买，
    这三件事的含义完全不同，必须逐项摆出来。
  · 两份 raw_snapshot 都用**当前 derive.py** 重算，避免拿旧版本 tide_data 直接比
    （10-21 改过 scale() 反向映射，跨版本比会得出假信号）。

输入：
  raw_snapshot.json                当日
  raw_snapshot_MMDD.json           历史（自动挑出当日之前最近的一天）
  data/daily/<YYYYMMDD>/industry_full.json   可选，全量行业（有则做精确轮动）

输出：delta.json
"""

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import derive          # noqa: E402

DAILY = os.path.join(HERE, "data", "daily")


# ---------------------------------------------------------------- 工具
def load_raw(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def pick_prev(cur_date):
    """当日之前最近的一份快照。

    两个来源都要扫：根目录 raw_snapshot_MMDD.json（未归档）+ data/daily/<YYYYMMDD>/raw_snapshot.json
    （archive_daily.py 归档后根目录那份就没了）。只扫根目录会跳过归档日 —— 11-19 就曾误比 11-17。
    """
    cands = {}
    for f in glob.glob(os.path.join(HERE, "raw_snapshot_*.json")):
        if not re.fullmatch(r"raw_snapshot_\d{4}\.json", os.path.basename(f)):
            continue
        try:
            d = load_raw(f)["tradeDate"]
        except Exception:                       # noqa: BLE001
            continue
        cands.setdefault(d, f)
    for f in glob.glob(os.path.join(DAILY, "*", "raw_snapshot.json")):
        try:
            d = load_raw(f)["tradeDate"]
        except Exception:                       # noqa: BLE001
            continue
        cands.setdefault(d, f)
    older = [d for d in cands if d < cur_date]
    if not older:
        return None
    d = max(older)
    return (d, cands[d])


def industry_map(date_str):
    """全量行业 {name: pct}；没有存档则返回 None。"""
    p = os.path.join(DAILY, date_str.replace("-", ""), "industry_full.json")
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    return {b["name"]: b["pct"] for b in d.get("items", [])}


def sample_rank(raw, n=15):
    """快照里的行业取样按涨幅降序取前 n（取样的 head 本身就是涨幅榜前 17）。"""
    bs = sorted(raw.get("industryBoards", []), key=lambda b: -b["pct"])
    return [(b["name"], b["pct"]) for b in bs[:n]]


def sign_txt(v, unit="", digits=0):
    if v is None:
        return "—"
    s = f"{v:+.{digits}f}{unit}" if isinstance(v, float) else f"{v:+d}{unit}"
    return s


# ---------------------------------------------------------------- 主体
def main():
    cur_path = os.path.join(HERE, "raw_snapshot.json")
    cur_raw = load_raw(cur_path)
    cur_date = cur_raw["tradeDate"]
    today = derive.build(cur_raw)

    prev = pick_prev(cur_date)
    if not prev:
        print("没有更早的快照，无法做逐日对比")
        return 1
    prev_date, prev_path = prev
    prev_raw = load_raw(prev_path)
    yday = derive.build(prev_raw)
    print(f"对比 {prev_date} → {cur_date}")

    out = {"asOf": cur_date, "prev": prev_date, "available": True}

    # ---- 1. 情绪 ----
    cf, pf = today["fear"], yday["fear"]
    parts = []
    for cp, pp in zip(cf["parts"], pf["parts"]):
        parts.append({"name": cp["name"], "now": cp["value"], "prev": pp["value"],
                      "weight": cp["weight"], "delta": cp["value"] - pp["value"]})
    out["fear"] = {
        "now": cf["value"], "prev": pf["value"], "delta": cf["value"] - pf["value"],
        "label": cf["label"], "labelPrev": pf["label"], "parts": parts,
    }

    # ---- 2. 指数 / 广度 / 成交 ----
    idx = []
    for c, p in zip(today["indices"], yday["indices"]):
        idx.append({
            "name": c["name"], "close": c["price"], "pct": c["pct"], "pctPrev": p["pct"],
            "deltaPct": round(c["pct"] - p["pct"], 2),
        })
    ct, pt = today["turnoverYi"], yday["turnoverYi"]
    out["market"] = {
        "index": idx,
        "turnoverYi": round(ct, 0), "turnoverPrev": round(pt, 0),
        "turnoverDelta": round(ct - pt, 0),
        "turnoverDeltaPct": round((ct / pt - 1) * 100, 1) if pt else None,
        "adv": today["breadth"]["advancers"], "advPrev": yday["breadth"]["advancers"],
        "dec": today["breadth"]["decliners"], "decPrev": yday["breadth"]["decliners"],
        "advRatio": today["breadth"]["advRatio"], "advRatioPrev": yday["breadth"]["advRatio"],
        "amplitude": today["amplitude"], "amplitudePrev": yday["amplitude"],
    }

    # ---- 3. 涨跌停 ----
    cl, pl = today["limit"], yday["limit"]
    out["limit"] = {
        "up": cl["upTotal"], "upPrev": pl["upTotal"], "upDelta": cl["upTotal"] - pl["upTotal"],
        "down": cl["downTotal"], "downPrev": pl["downTotal"], "downDelta": cl["downTotal"] - pl["downTotal"],
        "maxBoards": cl["maxBoards"], "maxBoardsPrev": pl["maxBoards"],
        "highBoardCount": cl["highBoardCount"], "highBoardCountPrev": pl["highBoardCount"],
        "sealedRatio": cl["sealedRatio"], "sealedRatioPrev": pl["sealedRatio"],
        "avgSealYi": cl["avgSealYi"], "avgSealYiPrev": pl["avgSealYi"],
    }

    # ---- 4. 板块轮动 ----
    cbs, pbs = today["boardStats"], yday["boardStats"]
    cmap, pmap = industry_map(cur_date), industry_map(prev_date)
    if cmap and pmap:
        # 全量行业：按当日涨幅取前 15，与上一日同一批行业的表现对照
        ctop = sorted(cmap.items(), key=lambda kv: -kv[1])[:15]
        ptop = sorted(pmap.items(), key=lambda kv: -kv[1])[:15]
        basis = "full"
    else:
        ctop, ptop = sample_rank(cur_raw, 15), sample_rank(prev_raw, 15)
        basis = "sample"
    pnames = {n for n, _ in ptop}
    cnames = {n for n, _ in ctop}
    pmap_use = pmap or dict(ptop)
    cmap_use = cmap or dict(ctop)
    # ⚠️ 涨跌家数只有「两日都是全样本」才能相减。11-17 之前 boardStats 是按 27 个取样
    # 统计的（那时报「16 涨 / 10 跌」），与全样本（72 涨 / 423 跌）差一个数量级，
    # 直接相减会得出「上涨行业从 16 增到 72」这种假信号。
    same_basis = (cbs.get("basis") or "sample") == (pbs.get("basis") or "sample")
    boards = {
        "basis": basis,
        "comparable": same_basis,
        "up": cbs["up"], "down": cbs["down"], "total": cbs.get("total"),
        "upPrev": pbs["up"], "downPrev": pbs["down"],
        "upDelta": (cbs["up"] - pbs["up"]) if same_basis else None,
        "downDelta": (cbs["down"] - pbs["down"]) if same_basis else None,
        "spread": cbs["spread"], "spreadPrev": pbs["spread"],
        "top": [{"name": n, "pct": round(v, 2),
                 "pctPrev": round(pmap_use.get(n), 2) if pmap_use.get(n) is not None else None,
                 "isNew": n not in pnames} for n, v in ctop],
        "dropped": [{"name": n, "pctPrev": round(v, 2),
                     "pct": round(cmap_use.get(n), 2) if cmap_use.get(n) is not None else None}
                    for n, v in ptop if n not in cnames],
    }
    out["boards"] = boards

    # ---- 5. 资金结构（机构 / 北向 / 营业部，按当日取样聚合） ----
    def agg_money(d):
        acc = {}
        for s in d["seats"]:
            a = acc.setdefault(s["kind"], {"net": 0.0, "buy": 0.0, "sell": 0.0, "n": 0})
            a["net"] += s["net"]
            a["buy"] += s["buy"]
            a["sell"] += s["sell"]
            a["n"] += 1
        return acc

    cm, mprev = agg_money(today), agg_money(yday)
    out["money"] = [
        {"kind": k,
         "net": round(cm.get(k, {}).get("net", 0), 2),
         "prev": round(mprev.get(k, {}).get("net", 0), 2),
         "delta": round(cm.get(k, {}).get("net", 0) - mprev.get(k, {}).get("net", 0), 2),
         "sample": cm.get(k, {}).get("n", 0),
         "samplePrev": mprev.get(k, {}).get("n", 0)}
        for k in ("机构", "北向", "营业部")
    ]

    # ---- 6. 游资：滚动累计 + 当日净买 ----
    # ⚠️ 滚动 10 日累计额**跨日不能相减**：窗口每天整体前移一天，走掉的与新进的
    # 抵消不了，"19.69 - 16.03 = +3.66 亿"里混着窗口滑动的量。
    # 真正可比的是「当日净买额」（同一天、同一口径），所以两者都出，前端用当日额对比。
    def profiles(d):
        ss = d.get("seatStocks") or {}
        return {p["name"]: p for p in ss.get("profiles") or []}

    def day_net(profile, date):
        """该游资当日上榜的净买额（亿元）。

        ⚠️ 当日**没有上榜记录**与「上榜了但买卖相抵」是两件事，都返回 0 会让人
        误读成"在观望"。没记录返回 None，前端显示破折号。
        """
        if not profile:
            return None
        tot, hit = 0.0, False
        for st in profile.get("stocks") or []:
            for dd in st.get("daily") or []:
                if dd.get("d") == date:
                    hit = True
                    tot += float(dd.get("buy") or 0) - float(dd.get("sell") or 0)
        return round(tot, 2) if hit else None

    c_date, p_date = cur_date, prev_date
    cp, pp = profiles(today), profiles(yday)
    hm = []
    for name in [n for n in cp] or [n for n in pp]:
        c, p = cp.get(name), pp.get(name)
        dn, dp = day_net(c, c_date), day_net(p, p_date)
        hm.append({
            "name": name,
            "net": c["net"] if c else None,
            "netPrev": p["net"] if p else None,
            "dayNet": dn,
            "dayPrev": dp,
            "dayDelta": round(dn - dp, 2) if dn is not None and dp is not None else None,
            "stocks": c.get("stockCount") if c else None,
            "activeDays": c.get("activeDays") if c else None,
            "topStock": (c["stocks"][0]["name"] if c and c.get("stocks") else None),
        })
    hm.sort(key=lambda x: (x["dayNet"] is None, -(x["dayNet"] or 0)))
    out["hotmoney"] = hm
    out["hotmoneyWindow"] = ((today.get("seatStocks") or {}).get("window") or {})

    # ---- 7. 结论信号（全部由数字推导，不写套话） ----
    sig = []

    def add(tone, text):
        sig.append({"tone": tone, "text": text})

    df = out["fear"]["delta"]
    if abs(df) >= 6:
        add("down" if df > 0 else "up", f"恐慌 {pf['value']} → {cf['value']}（{sign_txt(df)}），情绪明显切换")
    elif abs(df) >= 2:
        add("down" if df > 0 else "up", f"恐慌 {pf['value']} → {cf['value']}（{sign_txt(df)}），小幅变动")
    else:
        add("flat", f"恐慌 {pf['value']} → {cf['value']}，基本持平")

    dd = out["limit"]["downDelta"]
    if dd > 0:
        add("down", f"跌停 {pl['downTotal']} → {cl['downTotal']} 家（{sign_txt(dd)}），风险在扩散")
    elif dd < 0:
        add("up", f"跌停 {pl['downTotal']} → {cl['downTotal']} 家（{sign_txt(dd)}），风险收敛")
    else:
        add("flat", f"跌停维持 {cl['downTotal']} 家")

    du = out["limit"]["upDelta"]
    add("up" if du > 0 else ("down" if du < 0 else "flat"),
        f"涨停 {pl['upTotal']} → {cl['upTotal']} 家（{sign_txt(du)}）")

    if cl["maxBoards"] != pl["maxBoards"]:
        add("up" if cl["maxBoards"] > pl["maxBoards"] else "down",
            f"最高板 {pl['maxBoards']} → {cl['maxBoards']} 板，空间{'打开' if cl['maxBoards'] > pl['maxBoards'] else '压缩'}")
    else:
        add("flat", f"最高板维持 {cl['maxBoards']} 板（3 板以上 {cl['highBoardCount']} 家）")

    inst_c = next((m for m in out["money"] if m["kind"] == "机构"), None)
    if inst_c:
        # ⚠️ 「机构专用」不是一家营业部，而是交易所对机构买卖盘的统一标注 ——
        # 所以席位数恒为 1，真正的样本量是被合并的记录条数。两个交易日的机构合计
        # 可以直接比（口径相同），但别写成「1 席」，那会让人以为是单个机构的大单。
        if inst_c["prev"] < 0 <= inst_c["net"]:
            add("up", f"机构合计由净卖 {abs(inst_c['prev'])} 亿转为净买 {inst_c['net']} 亿")
        elif inst_c["net"] < 0 <= inst_c["prev"]:
            add("down", f"机构合计由净买 {inst_c['prev']} 亿转为净卖 {abs(inst_c['net'])} 亿")
        else:
            add("down" if inst_c["net"] < 0 else "up",
                f"机构合计净额 {sign_txt(inst_c['prev'], ' 亿', 2)} → {sign_txt(inst_c['net'], ' 亿', 2)}")

    if boards["top"]:
        fresh = [b["name"] for b in boards["top"][:8] if b["isNew"]]
        if fresh:
            add("info", f"涨幅前八新进 {len(fresh)} 个：{'、'.join(fresh[:4])}"
                        + ("…" if len(fresh) > 4 else ""))
        if same_basis:
            add("info", f"全市场上涨行业 {pbs['up']} → {cbs['up']} 个，"
                        f"下跌 {pbs['down']} → {cbs['down']} 个")
        else:
            add("info", f"行业涨跌家数今日起改为全样本口径（{cbs.get('total')} 个），"
                        f"与前一日的取样数不可直接相减")

    out["signals"] = sig

    p = os.path.join(HERE, "delta.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(f"delta.json 写出（{prev_date} → {cur_date}）")
    for s in sig:
        print(f"  [{s['tone']}] {s['text']}")
    print(f"  板块轮动基准：{'全量行业' if boards['basis'] == 'full' else '快照取样（无全量存档）'}"
          f" · 新进行业 {sum(1 for b in boards['top'] if b['isNew'])} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
