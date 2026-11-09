# -*- coding: utf-8 -*-
"""
潮汐 · 游资席位 — 个股明细抓取器
=================================
目的：把「龙虎榜席位」和「游资大佬」打通。

东方财富龙虎榜明细接口（RPT_BILLBOARD_DAILYDETAILSBUY / SELL）每条记录是
「某交易日 · 某席位 · 某只股票」的买卖金额，本身就能拼出「谁在买什么」。
本脚本拉最近 N 个交易日全量，聚合为：

    seatStocks.profiles   知名游资档案 × 命中席位 × 近期买入个股
    seatStocks.topSeats   近 N 日净买入最大的营业部席位 Top10
    seatStocks.stockFlow  个股维度：近 N 日被哪些游资买过（反向索引）

红线：
  - 席位与游资人名的对应是**市场公开共识**，非官方披露，前端必须注明；
  - 金额一律来自接口原始字段（万元），本脚本统一换算成亿元；
  - 不做任何估算、不补零。

输出：合并写入 raw_snapshot.json 的 seatStocks 字段（保留其余字段不动）。
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = "https://datacenter-web.eastmoney.com"
BASE = "https://push2.eastmoney.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

WINDOW_DAYS = 10          # 回看交易日（自然日跨度由接口返回决定，实际按交易日集合计算）

# ---------------------------------------------------------------- 游资档案
# aliases 用「券商 + 城市/路名」双段正则，避免「上海分公司」这类泛词误伤。
# 归属为市场公开共识，页面上必须标注「非官方认定」。
PROFILES = [
    {
        "name": "章盟主",
        "initials": "章",
        "aliases": [r"国泰海通.*江苏路", r"国泰君安.*江苏路"],
        "tags": ["容量核心", "隔日切换"],
        "hold": "3–5 日",
        "style": "偏好液冷/热管理/CPO/PCB 等行业主线容量核心，单日多票、分散打板后隔日切换，不锁仓。",
    },
    {
        "name": "桑田路",
        "initials": "桑",
        "aliases": [r"国盛证券.*桑田路"],
        "tags": ["高位抱团", "题材切换"],
        "hold": "2–4 日",
        "style": "以高位抱团代替梯队接力：在少数票上反复聚集，题材切换快，公开榜少见连续多日锁仓。",
    },
    {
        "name": "炒股养家",
        "initials": "养",
        "aliases": [r"中信证券.*溧阳路"],
        "tags": ["情绪周期", "择时"],
        "hold": "5–8 日",
        "style": "通道优势 + 一字 + 隔日高位兑现 + 低位挖掘，情绪周期心法出处。择时权重高于选股，冰点/退潮休息。",
    },
    {
        "name": "赵老哥",
        "initials": "赵",
        "aliases": [r"华鑫证券.*上海分公司", r"中国银河.*绍兴鲁迅中路"],
        "tags": ["龙头接力", "趋势加速"],
        "hold": "1–2 日",
        "style": "教科书式趋势加速板与龙头接力。公开榜沉默本身就是信号——「二板接力」代表人物不在场时，不宜重仓打板。",
    },
]

# 非营业部口径：东财把「其他」类合计成这几项，不能当游资席位
EXCLUDE_SEAT = ("中小投资者", "自然人", "机构专用", "沪股通", "深股通", "总部")

# 同一营业部在接口里有多种写法（「上海长宁区江苏路」/「长宁区江苏路」），
# 归一化后仍差一个城市名前缀。做二级归并：去掉城市名再比较。
CITY_WORDS = (
    "上海", "北京", "深圳", "广州", "杭州", "宁波", "南京", "武汉", "成都", "西安",
    "苏州", "绍兴", "无锡", "合肥", "长沙", "郑州", "天津", "重庆", "青岛", "厦门",
    "福州", "济南", "沈阳", "大连", "昆明", "南宁", "南昌", "贵阳", "太原", "石家庄",
    "哈尔滨", "长春", "兰州", "西宁", "银川", "乌鲁木齐", "呼和浩特", "海口", "拉萨",
    "温州", "嘉兴", "台州", "金华", "泉州", "东莞", "佛山", "珠海", "中山", "惠州",
    "常州", "南通", "徐州", "扬州", "盐城", "泰州", "镇江", "淮安", "湖州", "衢州",
    "舟山", "芜湖", "淄博", "烟台", "潍坊", "洛阳", "唐山", "保定", "漳州", "汕头",
)


def merge_key(norm_name):
    """在归一化名字上去掉城市词，得到可用于合并的键。

    ⚠️ 只对「营业部」剥城市名。分公司（如「华鑫证券上海分公司」）里城市就是
    唯一标识，剥掉会把上海/杭州/泉州/深圳/西安五家并成一个席位 —— 曾因此
    让「赵老哥」误挂 5 个城市的华鑫分公司，金额直接翻倍。
    """
    if "分公司" in norm_name:
        return norm_name
    k = norm_name
    for c in CITY_WORDS:
        k = k.replace(c, "")
    return k


def is_bond(code):
    """可转债（11xxxx / 12xxxx）不是股票，不纳入游资个股统计。"""
    return code.startswith(("11", "12", "13"))


def get_json(url, referer="https://data.eastmoney.com/", retries=3):
    headers = {"User-Agent": UA, "Accept": "*/*", "Referer": referer}
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                      # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"拉取失败 {url[:90]} -> {last}")


def fetch_detail(report, date_from):
    """拉全量明细（分页）。"""
    out, page = [], 1
    while True:
        filt = urllib.parse.quote(f"(TRADE_DATE>='{date_from}')")
        url = (f"{DATA}/api/data/v1/get?reportName={report}&columns=ALL"
               f"&filter={filt}&pageNumber={page}&pageSize=500&source=WEB&client=WEB")
        res = get_json(url).get("result")
        if not res:
            raise RuntimeError(f"{report} 返回空")
        out.extend(res["data"])
        if page >= res["pages"]:
            break
        page += 1
        time.sleep(0.3)
    return out


def norm_seat(name):
    """归一化席位名：去掉「股份/有限/公司/证券营业部」等噪声，只保留可比较主体。

    东财同一营业部存在多种写法（如「上海长宁区江苏路证券营业部」与
    「长宁区江苏路证券营业部」），归一化后合并，避免金额被拆成两半。
    """
    n = name
    for junk in ("股份有限公司", "有限责任公司", "有限公司", "证券营业部", "营业部", "证券"):
        n = n.replace(junk, "")
    return n.strip()


def setcode_of(secucode):
    """000520.SZ -> ('000520','0')；用于行情接口 secid。"""
    code, _, mkt = secucode.partition(".")
    if mkt == "SH":
        return code, "1"
    if mkt == "BJ":
        return code, "2"
    return code, "0"


def fetch_quotes(codes):
    """批量取股票名称 / 最新价 / 涨跌幅。codes: {code: setcode}"""
    out, items = {}, list(codes.items())
    for i in range(0, len(items), 80):
        chunk = items[i:i + 80]
        secids = ",".join(f"{sc}.{c}" for c, sc in chunk)
        url = (f"{BASE}/api/qt/ulist.np/get?fltt=2&invt=2&secids={secids}"
               "&fields=f2,f3,f12,f14")
        try:
            d = get_json(url, referer="https://quote.eastmoney.com/")
            for r in (d.get("data") or {}).get("diff") or []:
                def _num(v):
                    try:
                        return round(float(v), 3)
                    except (TypeError, ValueError):
                        return None
                out[r["f12"]] = {"name": r["f14"],
                                 "close": _num(r["f2"]), "pct": _num(r["f3"])}
        except Exception as e:                      # noqa: BLE001
            print(f"    [!] 行情批次失败 {i}: {e}")
        time.sleep(0.35)
    return out


def main():
    snap_path = os.path.join(HERE, "raw_snapshot.json")
    with open(snap_path, "r", encoding="utf-8") as fh:
        snap = json.load(fh)
    trade_date = snap["tradeDate"]
    print(f"快照交易日 {trade_date}")

    # 交易日窗口：从当日往前推 WINDOW_DAYS*1.8 个自然日，接口返回后按实际交易日截断
    end = datetime.strptime(trade_date, "%Y-%m-%d")
    start = end.fromordinal(end.toordinal() - int(WINDOW_DAYS * 1.9))
    date_from = start.strftime("%Y-%m-%d")
    print(f"抓取区间 {date_from} ~ {trade_date}")

    print("[1/4] 拉取买入席位明细 ...")
    buys = fetch_detail("RPT_BILLBOARD_DAILYDETAILSBUY", date_from)
    print(f"      {len(buys)} 条")
    print("[2/4] 拉取卖出席位明细 ...")
    sells = fetch_detail("RPT_BILLBOARD_DAILYDETAILSSELL", date_from)
    print(f"      {len(sells)} 条")

    # ---- 按交易日集合截断到最近 WINDOW_DAYS 个交易日 ----
    all_dates = sorted({r["TRADE_DATE"][:10] for r in buys + sells})
    keep = set(all_dates[-WINDOW_DAYS:])
    buys = [r for r in buys if r["TRADE_DATE"][:10] in keep]
    sells = [r for r in sells if r["TRADE_DATE"][:10] in keep]
    print(f"      交易日 {len(keep)} 个：{min(keep)} ~ {max(keep)}")

    # ---- 聚合：席位 -> 股票 -> 买卖 ----
    # agg[norm_seat] = {raw: {原写法}, buy, sell, days:set, stocks:{code:{...}}}
    agg = defaultdict(lambda: {"raw": set(), "buy": 0.0, "sell": 0.0,
                               "days": set(), "stocks": {}})
    stock_meta = {}     # code -> {secucode}
    for rows, side in ((buys, "buy"), (sells, "sell")):
        for r in rows:
            code, sc = setcode_of(r["SECUCODE"])
            if is_bond(code):          # 可转债不计入「游资买入个股」
                continue
            stock_meta[code] = sc
            k = merge_key(norm_seat(r["OPERATEDEPT_NAME"]))
            a = agg[k]
            a["raw"].add(r["OPERATEDEPT_NAME"])
            amt = float(r[side.upper()] or 0) / 1e8      # 万元 -> 亿元
            a[side] += amt
            d = r["TRADE_DATE"][:10]
            a["days"].add(d)
            st = a["stocks"].setdefault(code, {
                "buy": 0.0, "sell": 0.0, "times": 0, "lastDate": "",
                "close": None, "pct": None, "daily": {},
            })
            st[side] += amt
            if side == "buy":
                st["times"] += 1
            if d >= st["lastDate"]:
                st["lastDate"] = d
                st["close"] = r.get("CLOSE_PRICE")
                st["pct"] = r.get("CHANGE_RATE")
            # 同一席位同一股票同日可能因多个上榜原因出现多次，按日累加而非追加
            day = st["daily"].setdefault(d, {"d": d, "buy": 0.0, "sell": 0.0})
            day[side] += amt
    print(f"      聚合 {len(agg)} 个归一化席位，涉及 {len(stock_meta)} 只股票")

    # ---- 补股票名称 ----
    print("[3/4] 批量补股票名称 ...")
    quotes = fetch_quotes(stock_meta)
    print(f"      取到 {len(quotes)} 只股票行情")
    missing = len(stock_meta) - len(quotes)
    if missing:
        print(f"      [!] {missing} 只未取到行情，名称将留空（不猜）")

    # ---- 游资档案匹配 ----
    def match_profile(a_raw_name):
        hits = []
        for p in PROFILES:
            for pat in p["aliases"]:
                if re.search(pat, a_raw_name):
                    hits.append(p["name"])
                    break
        return hits

    profiles_out = []
    for p in PROFILES:
        seats, buy, sell, days, stocks = [], 0.0, 0.0, set(), {}
        for k, a in agg.items():
            if p["name"] not in match_profile(" ".join(sorted(a["raw"]))):
                continue
            seats.extend(sorted(a["raw"]))
            buy += a["buy"]
            sell += a["sell"]
            days |= a["days"]
            for code, st in a["stocks"].items():
                t = stocks.setdefault(code, {"buy": 0.0, "sell": 0.0, "times": 0,
                                             "lastDate": "", "daily": {}})
                t["buy"] += st["buy"]
                t["sell"] += st["sell"]
                t["times"] += st["times"]
                t["lastDate"] = max(t["lastDate"], st["lastDate"])
                for d, b in st["daily"].items():
                    dd = t["daily"].setdefault(d, {"d": d, "buy": 0.0, "sell": 0.0})
                    dd["buy"] += b["buy"]
                    dd["sell"] += b["sell"]
        row_stocks = []
        for code, t in stocks.items():
            q = quotes.get(code, {})
            daily = [{"d": v["d"], "buy": round(v["buy"], 2), "sell": round(v["sell"], 2)}
                     for v in sorted(t["daily"].values(), key=lambda x: x["d"])]
            row_stocks.append({
                "code": code,
                "name": q.get("name") or "",
                "buy": round(t["buy"], 2),
                "sell": round(t["sell"], 2),
                "net": round(t["buy"] - t["sell"], 2),
                "times": t["times"],
                "lastDate": t["lastDate"],
                "close": q.get("close"),
                "pct": q.get("pct"),
                "daily": daily[-6:],
            })
        row_stocks.sort(key=lambda x: (-x["buy"], -x["times"]))
        profiles_out.append({
            "name": p["name"], "initials": p["initials"],
            "tags": p["tags"], "hold": p["hold"], "style": p["style"],
            "seats": seats,
            "matched": bool(seats),
            "buy": round(buy, 2), "sell": round(sell, 2), "net": round(buy - sell, 2),
            "activeDays": len(days),
            "stockCount": len(stocks),
            "stocks": row_stocks[:8],
        })
        flag = "命中" if seats else "未命中"
        print(f"      {p['name']:<6} {flag}  买 {buy:6.2f} 亿 卖 {sell:6.2f} 亿 "
              f"净 {buy - sell:+6.2f} 亿 · {len(stocks)} 只 · {len(days)} 天")

    # ---- 近 N 日最活跃营业部席位 Top10 ----
    rows = []
    for k, a in agg.items():
        raw = sorted(a["raw"], key=len)[-1]
        if any(x in raw for x in EXCLUDE_SEAT):
            continue
        rows.append({
            "seat": raw,
            "aliases": sorted(a["raw"]),
            "buy": round(a["buy"], 2),
            "sell": round(a["sell"], 2),
            "net": round(a["buy"] - a["sell"], 2),
            "activeDays": len(a["days"]),
            "stockCount": len(a["stocks"]),
            "stocks": [{"code": c, "name": quotes.get(c, {}).get("name", ""),
                        "buy": round(s["buy"], 2), "times": s["times"],
                        "lastDate": s["lastDate"]}
                       for c, s in sorted(a["stocks"].items(),
                                          key=lambda kv: -kv[1]["buy"])[:5]],
        })
    rows.sort(key=lambda x: -x["net"])
    top_seats = rows[:10]
    print(f"      近 {len(keep)} 日净买 Top1：{top_seats[0]['seat']} "
          f"{top_seats[0]['net']:+.2f} 亿 / {top_seats[0]['stockCount']} 只")

    # ---- 个股维度反向索引：哪些票被游资席位净买入 ----
    # 用途：筛选池的 A4 加分项（近 10 日被游资买入）直接查这张表，不再人工标注。
    stock_hot = {}
    for k, a in agg.items():
        raw = sorted(a["raw"], key=len)[-1]
        if any(x in raw for x in EXCLUDE_SEAT):
            continue
        for code, st in a["stocks"].items():
            h = stock_hot.setdefault(code, {
                "code": code, "buy": 0.0, "sell": 0.0,
                "seatCount": 0, "seats": [], "lastDate": "",
            })
            h["buy"] += st["buy"]
            h["sell"] += st["sell"]
            if st["buy"] - st["sell"] > 0:
                h["seatCount"] += 1
                if len(h["seats"]) < 4:
                    h["seats"].append(raw)
            h["lastDate"] = max(h["lastDate"], st["lastDate"])
    for code, h in stock_hot.items():
        h["buy"] = round(h["buy"], 2)
        h["sell"] = round(h["sell"], 2)
        h["net"] = round(h["buy"] - h["sell"], 2)
        h["name"] = quotes.get(code, {}).get("name", "")
    hot_all = [h for h in stock_hot.values() if h["net"] > 0]
    hot_rows = sorted(hot_all, key=lambda x: -x["net"])[:80]
    print(f"      个股热度 {len(stock_hot)} 只，净买入为正 {len(hot_all)} 只，取前 {len(hot_rows)}")

    # ---- 写回快照 ----
    print("[4/4] 写回 raw_snapshot.json ...")
    snap["seatStocks"] = {
        "window": {"from": min(keep), "to": max(keep), "days": len(keep)},
        "note": "席位与人名的对应为市场公开共识，非交易所或券商官方披露；金额取自东方财富龙虎榜明细，单位亿元；已剔除可转债。",
        "profiles": profiles_out,
        "topSeats": top_seats,
        "stockHot": hot_rows,
    }
    with open(snap_path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=2)
    print(f"完成。seatStocks 写入 {len(profiles_out)} 个游资档案 / {len(top_seats)} 个活跃席位")

    # 自检：命中游资的买入个股金额不得超过该席位总买入（防止重复累加）
    for p in profiles_out:
        tot = sum(s["buy"] for s in p["stocks"])
        assert tot <= p["buy"] + 0.05, f"{p['name']} 个股买入合计 {tot} > 席位买入 {p['buy']}"
    return 0


if __name__ == "__main__":
    sys.exit(main())
