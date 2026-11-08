# -*- coding: utf-8 -*-
"""
用东方财富公开接口补齐 11-06 缺失的三块数据。

背景：通达信 MCP 配额耗尽（leftValue=2），指数 / 涨停池 / 跌停池 已从通达信
抓取并核对，存于 raw_snapshot_1106_partial.json。本脚本补齐：
    1. industryBoards    —— 行业板块（东财口径，496 个二级行业 → 取涨幅榜前 17 + 榜尾 10）
    2. seatActivity      —— 龙虎榜席位活跃度
    3. stockListActivity —— 龙虎榜个股活跃度
    4. watchlist         —— 观察池 5 只，含 60 日真实 K 线（现抓现填，禁止臆造）

⚠️ 口径说明
    东财行业分类比通达信二级行业更细（496 vs ~100），板块数值与历史快照
    不可直接比较。指数 / 涨跌停仍为通达信原始数据，未做任何替换。

单位约定：seatActivity / stockListActivity 一律存「万元」，与 11-04 快照一致，
derive.py 内部除以 1e4 转为亿元。东财接口返回「元」，本脚本负责换算。
"""

import json
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime

TRADE_DATE = "2026-11-06"
BASE = "https://push2.eastmoney.com"
DATA = "https://datacenter-web.eastmoney.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get_json(url, referer=None, retries=3):
    """带重试的 JSON 拉取。东财偶发限流，退避重试。"""
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                        # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"拉取失败 {url[:90]} -> {last}")


# ============================================================
# 1. 行业板块
# ============================================================
def fetch_boards():
    """取东财行业板块全量，返回 (涨幅榜前17 + 榜尾10) 共 27 个。

    ⚠️ 东财 clist 单页硬上限 100 条，pz 传 500 只会回 100。必须翻页拿全量，
    否则「榜尾 10」拿到的是涨幅榜第 91–100 名，而非真正的跌幅榜。
    """
    rows, page, total = [], 1, None
    while True:
        url = (f"{BASE}/api/qt/clist/get?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2"
               "&fid=f3&fs=m:90+t:2"
               "&fields=f12,f14,f3,f8,f104,f105,f106,f9")
        d = get_json(url)["data"]
        total = d["total"]
        batch = d["diff"]
        if not batch:
            break
        rows.extend(batch)
        if len(rows) >= total:
            break
        page += 1
        time.sleep(0.35)
    print(f"  东财行业板块共 {total} 个，翻 {page} 页取到 {len(rows)} 个")
    assert len(rows) >= total, f"翻页不全：{len(rows)}/{total}"

    def conv(r):
        return {
            "code": r["f12"],                     # BK 代码，前端不消费，仅留档
            "name": r["f14"],
            "pct": round(float(r["f3"]), 2),
            "turnover": round(float(r["f8"]), 2),
            "advancers": int(r["f104"]),
            "decliners": int(r["f105"]),
            "pe": round(float(r["f9"]), 4) if isinstance(r["f9"], (int, float)) else None,
        }

    rows = [conv(r) for r in rows]
    head = rows[:17]
    tail = rows[-10:]
    out = head + tail
    print(f"  取样：涨幅榜前 17（{head[0]['name']} {head[0]['pct']:+.2f}%）"
          f" + 榜尾 10（{tail[-1]['name']} {tail[-1]['pct']:+.2f}%）")
    up = sum(1 for b in rows if b["pct"] > 0)
    down = sum(1 for b in rows if b["pct"] < 0)
    print(f"  全样本：上涨 {up} 个 / 下跌 {down} 个")
    return out


# ============================================================
# 2. 席位活跃度
# ============================================================
def fetch_seats():
    """龙虎榜席位活跃度（含机构专用 / 沪深股通）。

    ⚠️ 不用 RPT_OPERATEDEPT_ACTIVE：那张表只统计「营业部」，机构专用与
    沪深股通专用不是营业部，会被整体漏掉（实测 199 条里「机构」命中 0 条）。
    改用买入/卖出前五席位明细聚合，能覆盖全部席位类型。

    ⚠️ lists 语义：东财给的是「当日上榜的股票只数」，通达信原始快照给的是
    「累计上榜天数」（11-04 机构专用 3726）。两者量纲完全不同，前端文案
    必须写「当日上榜 N 只」，不能沿用「上榜 N 次」。
    """
    def fetch_detail(report):
        out, page, pages = [], 1, None
        while True:
            url = (f"{DATA}/api/data/v1/get?reportName={report}&columns=ALL"
                   f"&filter={urllib.parse.quote(chr(40) + 'TRADE_DATE=' + chr(39) + TRADE_DATE + chr(39) + chr(41))}"
                   f"&pageNumber={page}&pageSize=500&source=WEB&client=WEB")
            d = get_json(url, referer="https://data.eastmoney.com/")
            res = d.get("result")
            if not res:
                raise RuntimeError(f"{report} 返回空：{d.get('message')}")
            pages = res["pages"]
            out.extend(res["data"])
            if page >= pages:
                break
            page += 1
            time.sleep(0.35)
        print(f"    {report}: {pages} 页，{len(out)} 条")
        return out

    buys = fetch_detail("RPT_BILLBOARD_DAILYDETAILSBUY")
    sells = fetch_detail("RPT_BILLBOARD_DAILYDETAILSSELL")

    agg, stocks = {}, {}
    for r in buys:
        k = r.get("OPERATEDEPT_CODE") or r["OPERATEDEPT_NAME"]
        a = agg.setdefault(k, {"name": r["OPERATEDEPT_NAME"], "buy": 0.0, "sell": 0.0})
        a["buy"] += float(r["BUY"] or 0)
        stocks.setdefault(k, set()).add(r["SECURITY_CODE"])
    for r in sells:
        k = r.get("OPERATEDEPT_CODE") or r["OPERATEDEPT_NAME"]
        a = agg.setdefault(k, {"name": r["OPERATEDEPT_NAME"], "buy": 0.0, "sell": 0.0})
        a["sell"] += float(r["SELL"] or 0)
        stocks.setdefault(k, set()).add(r["SECURITY_CODE"])

    seats = []
    for k, a in agg.items():
        n = len(stocks.get(k, ()))
        seats.append({
            "seat": normalize_seat(a["name"]),
            "turnover": round((a["buy"] + a["sell"]) / 1e4, 2),   # 万元
            "lists": n,                                          # 当日上榜股票数
            "buyCount": sum(1 for r in buys if (r.get("OPERATEDEPT_CODE") or r["OPERATEDEPT_NAME"]) == k),
            "buyAmount": round(a["buy"] / 1e4, 2),               # 万元
            "sellCount": sum(1 for r in sells if (r.get("OPERATEDEPT_CODE") or r["OPERATEDEPT_NAME"]) == k),
            "sellAmount": round(a["sell"] / 1e4, 2),             # 万元
            "net": round((a["buy"] - a["sell"]) / 1e4, 2),       # 万元
        })
    print(f"  聚合出 {len(seats)} 个席位")

    seats.sort(key=lambda s: s["net"], reverse=True)
    picked, used = [], set()

    def take(s):
        if s["seat"] in used or len(picked) >= 10:
            return
        used.add(s["seat"])
        picked.append(s)

    for s in seats[:5]:                                # 最大买方
        take(s)
    for s in seats[-3:]:                               # 最大卖方
        take(s)
    for kw in ("机构专用", "沪股通", "深股通"):          # 关键性质席位
        for s in seats:
            if kw in s["seat"]:
                take(s)
                break
    for s in seats:                                    # 补足
        take(s)

    picked.sort(key=lambda s: s["net"], reverse=True)
    print(f"  取样 {len(picked)} 个席位，净买额区间 "
          f"{picked[0]['net']/1e4:+.2f} 亿 ~ {picked[-1]['net']/1e4:+.2f} 亿")
    kinds = {}
    for s in picked:
        k = "机构" if "机构专用" in s["seat"] else ("北向" if "股通" in s["seat"] else "营业部")
        kinds[k] = kinds.get(k, 0) + 1
    print(f"  席位构成：{kinds}")
    return picked


def normalize_seat(name):
    """东财全称 → 通达信风格简称，保证前端 `seat.includes(seatKey)` 能命中。

    seatKey 依赖的关键词：国泰海通 / 拉萨 / 中信证券 / 机构专用 / 股通。
    东财 ORG_NAME_ABBR 已带券商前缀，只需去掉「证券营业部」「股份有限公司」等尾巴。
    """
    n = name.replace("股份有限公司", "").replace("有限公司", "")
    n = n.replace("证券营业部", "").replace("证券营业", "").replace("营业部", "")
    return n.strip()


# ============================================================
# 3. 个股活跃度（龙虎榜个股）
# ============================================================
def fetch_stock_activity():
    """龙虎榜个股明细 → 按股票聚合上榜次数与买卖金额。"""
    url = (f"{DATA}/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW"
           f"&columns=ALL&filter={urllib.parse.quote(chr(40) + 'TRADE_DATE=' + chr(39) + TRADE_DATE + chr(39) + chr(41))}"
           "&pageNumber=1&pageSize=200&source=WEB&client=WEB")
    d = get_json(url, referer="https://data.eastmoney.com/")
    rows = d["result"]["data"]
    print(f"  龙虎榜个股明细 {len(rows)} 条")

    agg = {}
    for r in rows:
        code = r["SECURITY_CODE"]
        mkt = r.get("MARKET", "")
        setcode = {"SH": "1", "SZ": "0", "BJ": "2"}.get(mkt, "1")
        a = agg.setdefault(code, {
            "code": code, "setcode": setcode,
            "name": r["SECURITY_NAME_ABBR"],
            "lists": 0, "_buy": 0.0, "_sell": 0.0,
            "lastDate": r["TRADE_DATE"][:10],
        })
        a["lists"] += 1
        a["_buy"] += float(r["BILLBOARD_BUY_AMT"] or 0)
        a["_sell"] += float(r["BILLBOARD_SELL_AMT"] or 0)

    out = []
    for a in agg.values():
        out.append({
            "code": a["code"], "setcode": a["setcode"], "name": a["name"],
            "lists": a["lists"], "lastDate": a["lastDate"],
            "buyAmount": round(a["_buy"] / 1e4, 2),      # 万元
            "sellAmount": round(a["_sell"] / 1e4, 2),    # 万元
        })
    out.sort(key=lambda x: (x["lists"], x["buyAmount"]), reverse=True)
    out = out[:10]
    print(f"  聚合 {len(agg)} 只股票，取上榜次数前 10（首位 {out[0]['name']} {out[0]['lists']} 次）")
    return out


# ============================================================
# 4. 观察池 + 60 日 K 线
# ============================================================
WATCH = [
    ("600241", "1", "时代万恒", "电池"),
    ("002733", "0", "雄韬股份", "电池"),
    ("300821", "0", "东岳硅材", "化学制品"),
    ("300413", "0", "芒果超媒", "文化传媒"),
    ("605577", "1", "龙版传媒", "文化传媒"),
]


def em_secid(code, setcode):
    return f"{'1' if setcode == '1' else '0'}.{code}"


def fetch_quote(code, setcode):
    url = (f"{BASE}/api/qt/stock/get?secid={em_secid(code, setcode)}&fltt=2&invt=2"
           "&fields=f43,f57,f58,f168,f170")
    d = get_json(url)["data"]
    if not d:
        raise RuntimeError(f"{code} 行情为空")
    return {
        "price": round(float(d["f43"]), 2),
        "pct": round(float(d["f170"]), 2),
        "turnoverRate": round(float(d["f168"]), 2),
        "name": d["f58"],
    }


def fetch_kline(code, setcode, n=60):
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={em_secid(code, setcode)}&fields1=f1,f2,f3"
           "&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101"
           f"&lmt={n}")
    d = get_json(url)["data"]
    if not d or not d.get("klines"):
        raise RuntimeError(f"{code} K 线为空")
    out = []
    for line in d["klines"]:
        p = line.split(",")
        out.append({"d": p[0].replace("-", ""), "c": round(float(p[2]), 2)})
    return out


def fetch_watchlist():
    out = []
    for code, setcode, name, industry in WATCH:
        q = fetch_quote(code, setcode)
        kl = fetch_kline(code, setcode, 60)
        assert len(kl) == 60, f"{code} K 线只有 {len(kl)} 根"
        assert kl[-1]["d"] == TRADE_DATE.replace("-", ""), \
            f"{code} 末根 K 线日期 {kl[-1]['d']} != {TRADE_DATE}"
        out.append({
            "code": code, "setcode": setcode, "name": name, "industry": industry,
            "price": q["price"], "pct": q["pct"], "turnoverRate": q["turnoverRate"],
            "kline60": kl,
        })
        rng = (kl[-1]["c"] / kl[0]["c"] - 1) * 100
        print(f"  {code} {name}: {len(kl)} 根 {kl[0]['d']}→{kl[-1]['d']} "
              f"收 {kl[0]['c']}→{kl[-1]['c']} 区间 {rng:+.2f}% 现价 {q['price']} ({q['pct']:+.2f}%)")
        time.sleep(0.4)
    return out


# ============================================================
# main
# ============================================================
def main():
    # ---- 先验证东财返回的确实是 11-06 收盘数据 ----
    idx = get_json(f"{BASE}/api/qt/stock/get?secid=1.000001&fltt=2&invt=2"
                   "&fields=f43,f170")["data"]
    sh_close, sh_pct = round(float(idx["f43"]), 2), round(float(idx["f170"]), 2)
    print(f"东财上证指数：{sh_close} ({sh_pct:+.2f}%)")
    assert sh_close == 3649.18, f"东财返回 {sh_close}，与通达信 11-06 的 3649.18 不符，中止"
    assert sh_pct == -1.10, f"涨跌幅 {sh_pct} 与通达信 -1.10% 不符，中止"
    print("✅ 交叉验证通过：东财与通达信的上证收盘一致\n")

    partial = json.load(open("raw_snapshot_1106_partial.json", encoding="utf-8"))
    assert partial["tradeDate"] == TRADE_DATE

    print("[1/4] 行业板块")
    boards = fetch_boards()
    print("\n[2/4] 席位活跃度")
    seats = fetch_seats()
    print("\n[3/4] 个股活跃度")
    stock_act = fetch_stock_activity()
    print("\n[4/4] 观察池 K 线")
    watch = fetch_watchlist()

    snap = {
        "tradeDate": TRADE_DATE,
        "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "通达信(指数/涨跌停) + 东方财富(板块/龙虎榜/K线)",
        "sourceNote": ("指数与涨跌停池来自通达信 MCP；行业板块、龙虎榜席位、个股活跃度、"
                       "观察池 K 线因通达信配额耗尽，改用东方财富公开接口补齐。"
                       "东财行业分类为 496 个二级行业，与通达信 ~100 个二级行业口径不同，"
                       "板块数值与 11-04 及更早快照不可直接比较。"),
        "indices": partial["indices"],
        "industryBoards": boards,
        "limitUpPool": partial["limitUpPool"],
        "limitUpTotal": partial["limitUpTotal"],
        "limitDownPool": partial["limitDownPool"],
        "limitDownTotal": partial["limitDownTotal"],
        "seatActivity": seats,
        "stockListActivity": stock_act,
        "watchlist": watch,
    }

    # 硬校验：字段完整性
    assert len(snap["indices"]) == 3
    assert len(snap["industryBoards"]) == 27
    assert len(snap["seatActivity"]) == 10, f"席位 {len(snap['seatActivity'])} 条"
    assert len(snap["stockListActivity"]) == 10
    assert len(snap["watchlist"]) == 5
    assert all(len(w["kline60"]) == 60 for w in snap["watchlist"])
    assert snap["limitUpTotal"] >= len(snap["limitUpPool"])

    with open("raw_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)

    print(f"\nOK -> raw_snapshot.json")
    print(f"tradeDate={TRADE_DATE} 涨停={snap['limitUpTotal']} 跌停={snap['limitDownTotal']}")
    print(f"板块 {len(boards)} | 席位 {len(seats)} | 活跃股 {len(stock_act)} | 观察池 {len(watch)}")


if __name__ == "__main__":
    main()
