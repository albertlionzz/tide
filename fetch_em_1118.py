# -*- coding: utf-8 -*-
"""
组装 2026-11-18 的完整快照（raw_snapshot.json）。

背景：通达信 MCP 配额仍未恢复（quota is below 10, leftValue=2），
本轮全量改用两家公开接口，并做**双源交叉校验**：

    指数 / 涨跌家数  东方财富 push2 ulist      ← 上证收盘再用新浪财经二次核对
    行业板块         东方财富 clist            （496 个二级行业，翻页取全量）
    涨停池           东方财富 getTopicZTPool   ← 成交量纲（lbc/fbt/zbc/fund）
                     同花顺 limit_up_pool      ← 补「涨停原因」「板型（一字/T字/换手）」
    跌停池           东方财富 getTopicDTPool   ← 同花顺 lower_limit_pool 核对家数
    龙虎榜席位       东方财富 RPT_BILLBOARD_DAILYDETAILSBUY/SELL 聚合
    个股活跃度       东方财富 RPT_DAILYBILLBOARD_DETAILSNEW
    观察池 K 线      东方财富 push2his kline（fqt=1 前复权，与通达信口径一致）

⚠️ 口径说明（写进 sourceNote，前端页脚可见）
    1. 板块为东财 496 个二级行业，与通达信 ~100 个口径不同，不可与 10 月快照直接比较；
       但 11-06 起一直是东财口径，与 11-06 可比。
    2. 涨停原因取自同花顺 reason_type（用 + 连接），本脚本统一转成 . 分隔，
       与历史快照的写法一致（app.js 板块联动按 . 切分）。
    3. 备注列没有公司基本面简介（通达信独有字段），改为「行业 · 板型 · 首封时间」，
       全部为接口原始字段，不做任何文字生成。

单位约定：seatActivity / stockListActivity 一律存「万元」，derive.py 内部除以 1e4 转亿元。
"""

import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime

TRADE_DATE = "2026-11-18"
BASE = "http://push2.eastmoney.com"
HIS = "http://push2his.eastmoney.com"
EX = "http://push2ex.eastmoney.com"
DATA = "https://datacenter-web.eastmoney.com"
THS = "https://data.10jqka.com.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CTX = ssl.create_default_context()
# 东财自 2026-11-18 起会直接断开「关闭证书校验」的连接（RemoteDisconnected），
# 必须用默认校验上下文
# CTX.check_hostname = False
# CTX.verify_mode = ssl.CERT_NONE


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache", TRADE_DATE)


def cached(name, fn):
    """分步缓存：东财会对高频请求断连（2026-11-18 实测），一次跑不完就把已成功的步骤留住，
    下次运行只补没成功的部分。缓存目录 _cache/<交易日>/。"""
    p = os.path.join(CACHE, name + ".json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = json.load(f)
            print(f"  [cache] {name}")
            return v
        except (json.JSONDecodeError, ValueError):
            print(f"  [cache] {name} 损坏，重新抓取")
            os.remove(p)
    v = fn()
    os.makedirs(CACHE, exist_ok=True)
    # ⚠️ 原子写：先写 .tmp 再 rename。fetch_dt 返回的第三项是 set，json 不能直接存，
    # 旧实现会在写到一半时抛 TypeError，留下一个半截文件 —— 下次读到就是 JSONDecodeError。
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(v, f, ensure_ascii=False,
                  default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    time.sleep(0.8)                       # 步骤之间强制间隔，降低触发频控概率
    return v


def get_json(url, referer=None, retries=3):
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                        # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"拉取失败 {url[:90]} -> {last}")


def get_text(url, referer=None, retries=3):
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("gbk", "ignore")
        except Exception as e:                        # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"拉取失败 {url[:90]} -> {last}")


def em_secid(code, setcode):
    return f"{'1' if setcode == '1' else '0'}.{code}"


def setcode_of(code):
    if code.startswith(("6", "9")):
        return "1"
    if code.startswith(("4", "8")):
        return "2"
    return "0"


def hhmmss(v):
    """东财 fbt/lbt 是整数 92500 这种，转 09:25:00。"""
    if not v:
        return ""
    s = str(int(v)).zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


# ============================================================
# 1. 三大指数 + 涨跌家数
# ============================================================
IDX = [("000001", "1", "上证指数"), ("399001", "0", "深证成指"), ("399006", "0", "创业板指")]


def fetch_indices():
    secids = ",".join(em_secid(c, s) for c, s, _ in IDX)
    url = (f"{BASE}/api/qt/ulist.np/get?fltt=2&invt=2&secids={secids}"
           "&fields=f2,f5,f6,f8,f12,f13,f14,f15,f16,f17,f18,f104,f105,f106")
    diff = get_json(url)["data"]["diff"]
    by_code = {r["f12"]: r for r in diff}

    out = []
    for code, setcode, name in IDX:
        r = by_code[code]
        now = float(r["f2"])
        prev = float(r["f18"])
        out.append({
            "code": code, "setcode": setcode, "name": name,
            "close": prev,                                    # 约定：close = 昨收
            "open": float(r["f17"]),
            "high": float(r["f15"]),
            "low": float(r["f16"]),
            "now": now,
            "prevClose": prev,
            "amount": float(r["f6"]),                         # 元
            "volume": int(r["f5"]),                           # 手
            "turnoverRate": float(r["f8"]),
            "advancers": int(r["f104"]),
            "decliners": int(r["f105"]),
            "unchanged": int(r["f106"]),
        })
        print(f"  {name:<6} {now:>10.2f}  {(now - prev) / prev * 100:+6.2f}%  "
              f"涨 {r['f104']:>4} 跌 {r['f105']:>4} 平 {r['f106']:>3}  "
              f"振幅 {(float(r['f15']) - float(r['f16'])) / prev * 100:.2f}%")
    return out


def qq_day(sh_symbol, date_iso):
    """腾讯日 K 里取指定交易日的 {open, close, high, low, prevClose}。

    用途：盘中运行时，新浪/东财实时接口给的是**更新的交易日**，没法用来核对
    「上一交易日收盘」。东财 push2his 又常被频控断连，腾讯日 K 是最稳的同日口径。
    """
    d = get_json(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                 f"?param={sh_symbol},day,,,40,qfq", referer="https://gu.qq.com/")
    node = d["data"][sh_symbol]
    rows = node.get("qfqday") or node.get("day") or []
    for i, r in enumerate(rows):
        if r[0] == date_iso:
            return {"open": float(r[1]), "close": float(r[2]), "high": float(r[3]),
                    "low": float(r[4]),
                    "prevClose": float(rows[i - 1][2]) if i else None}
    raise RuntimeError(f"腾讯日K里没有 {date_iso}")


def cross_check_index(indices):
    """二次核对上证收盘/高低点。

    ⚠️ 只在「两源指向同一交易日」时直接比对。盘中跑脚本时，新浪给的是**今天**的
    盘中价，而东财 ulist 给的是**上一交易日**收盘 —— 直接相减会报出
    「东财 3587.83 vs 新浪 3571.99」，看着像数据错，其实是两个不同交易日。
    日期不一致时改用腾讯日 K 的同一交易日核对。
    """
    txt = get_text("https://hq.sinajs.cn/list=sh000001", referer="https://finance.sina.com.cn")
    seg = txt.split('"')[1].split(",")
    o, prev, now, high, low = (float(seg[1]), float(seg[2]), float(seg[3]),
                               float(seg[4]), float(seg[5]))
    sina_date = seg[30] if len(seg) > 30 else ""
    sina_time = seg[31] if len(seg) > 31 else ""
    sh = indices[0]

    if sina_date and sina_date != TRADE_DATE:
        print(f"  新浪最新为 {sina_date} {sina_time}（盘中），与目标交易日 {TRADE_DATE} 不同 → 用腾讯日K核对")
        q = qq_day("sh000001", TRADE_DATE)
        print(f"  腾讯：{TRADE_DATE} 开{q['open']:.2f} 收{q['close']:.2f} "
              f"高{q['high']:.2f} 低{q['low']:.2f} 昨收{q['prevClose']:.2f}")
        assert abs(q["close"] - sh["now"]) < 0.03, f"上证收盘 东财 {sh['now']} vs 腾讯 {q['close']}"
        assert abs(q["prevClose"] - sh["prevClose"]) < 0.03, \
            f"上证昨收 东财 {sh['prevClose']} vs 腾讯 {q['prevClose']}"
        assert abs(q["high"] - sh["high"]) < 0.03 and abs(q["low"] - sh["low"]) < 0.03, "上证高低点不一致"
        print("  ✅ 双源一致（东财 / 腾讯日K，同一交易日）")
        return

    print(f"  新浪：上证 开{o:.2f} 昨收{prev:.2f} 现{now:.2f} 高{high:.2f} 低{low:.2f}")
    assert abs(now - sh["now"]) < 0.02, f"上证收盘 东财 {sh['now']} vs 新浪 {now}"
    assert abs(prev - sh["prevClose"]) < 0.02, f"上证昨收 东财 {sh['prevClose']} vs 新浪 {prev}"
    assert abs(high - sh["high"]) < 0.02 and abs(low - sh["low"]) < 0.02, "上证高低点不一致"
    print("  ✅ 双源一致（东财 / 新浪）")


# ============================================================
# 2. 行业板块
# ============================================================
def fetch_boards():
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
            "code": r["f12"],
            "name": r["f14"],
            "pct": round(float(r["f3"]), 2),
            "turnover": round(float(r["f8"]), 2),
            "advancers": int(r["f104"]),
            "decliners": int(r["f105"]),
            "pe": round(float(r["f9"]), 4) if isinstance(r["f9"], (int, float)) else None,
        }

    rows = [conv(r) for r in rows]
    head, tail = rows[:17], rows[-10:]
    print(f"  取样：涨幅榜前 17（{head[0]['name']} {head[0]['pct']:+.2f}%）"
          f" + 榜尾 10（{tail[-1]['name']} {tail[-1]['pct']:+.2f}%）")
    up = sum(1 for b in rows if b["pct"] > 0)
    down = sum(1 for b in rows if b["pct"] < 0)
    print(f"  全样本：上涨 {up} 个 / 下跌 {down} 个 / 平 {len(rows) - up - down} 个")
    # ⚠️ 快照只存「涨幅前 17 + 榜尾 10」这 27 个取样，涨跌家数若按取样统计，
    # 会出现「25 个行业 · 涨 16 / 跌 9」这种把取样当全市场的假象（真实是 496 个）。
    # 全样本统计单独落一份，derive 用它写 boardStats。
    stats = {
        "total": len(rows),
        "up": up,
        "down": down,
        "flat": len(rows) - up - down,
        "spread": round(rows[0]["pct"] - rows[-1]["pct"], 2),
    }
    return head + tail, stats, rows


# ============================================================
# 3. 涨停池（东财数值 + 同花顺原因/板型）
# ============================================================
def fetch_zt_em():
    url = (f"{EX}/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
           f"&Pageindex=0&pagesize=300&sort=fbt%3Aasc&date={TRADE_DATE.replace('-', '')}")
    d = get_json(url, referer="https://quote.eastmoney.com/")["data"]
    print(f"  东财涨停池 total={d['tc']} 条，返回 {len(d['pool'])} 条")
    return d["tc"], d["pool"]


def fetch_zt_ths():
    url = (f"{THS}/dataapi/limit_up/limit_up_pool?page=1&limit=100"
           "&field=199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
           "1968584,3475914,9003,9004"
           "&filter=HS,GEM2STAR&order_field=330324&order_type=0"
           f"&date={TRADE_DATE.replace('-', '')}")
    d = get_json(url, referer="https://data.10jqka.com.cn/")["data"]
    print(f"  同花顺涨停池 total={d['page']['total']} 条，返回 {len(d['info'])} 条")
    return d["page"]["total"], {r["code"]: r for r in d["info"]}


def build_limit_up():
    tc, pool = fetch_zt_em()
    ths_total, ths = fetch_zt_ths()
    # 同花顺 filter=HS,GEM2STAR 不含北交所，家数差 1~N 只时先定位差异再判定，
    # 不能直接放宽 assert —— 差异若出现在沪深股上，说明真有一源错漏，必须中止。
    only_em = {r["c"] for r in pool} - set(ths)
    bj = {c for c in only_em if c.startswith(("4", "8", "9"))}
    if only_em:
        print(f"  注：{len(only_em)} 只仅东财有（{sorted(only_em)}），"
              f"其中北交所 {len(bj)} 只（同花顺 filter 不含 BJ）")
    assert not (only_em - bj), f"沪深涨停家数不一致：仅东财有 {sorted(only_em - bj)}"
    assert not (set(ths) - {r["c"] for r in pool}), \
        f"仅同花顺有 {sorted(set(ths) - {r['c'] for r in pool})}"
    assert tc == ths_total + len(bj), f"涨停家数两源不一致：东财 {tc} vs 同花顺 {ths_total}+北交所{len(bj)}"

    rows = []
    for r in pool:
        code = r["c"]
        h = ths.get(code, {})
        reason = (h.get("reason_type") or "").strip()
        reason = reason.replace("+", ".").replace("、", ".").replace("，", ".")
        board_type = h.get("limit_up_type") or "换手板"
        fbt = hhmmss(r.get("fbt"))
        hybk = r.get("hybk") or ""
        zttj = r.get("zttj") or {}
        rows.append({
            "code": code,
            "setcode": setcode_of(code),
            "name": r["n"],
            "price": round(r["p"] / 1000, 2),               # 东财价格是 1/1000 元
            "pct": round(float(r["zdp"]), 2),
            "boards": int(r.get("lbc") or 1),
            # 东财 zttj 是「N 天 M 个涨停」，与连板数 lbc 不是一回事（11 天 7 板 ≠ 7 连板）。
            # 优先用同花顺 high_days（如 6天6板），它和 lbc 一致；缺失时才退回 zttj。
            "pattern": h.get("high_days") or f"{zttj.get('days', 1)}天{zttj.get('ct', 1)}板",
            "boardType": board_type,
            "sealAmount": int(r.get("fund") or 0),           # 元
            "firstSealTime": fbt,
            "openTimes": int(r.get("zbc") or 0),
            "reason": reason or hybk,                        # 无原因则退回行业名，不做编造
            "detail": " · ".join(x for x in (hybk, board_type, f"首封 {fbt}") if x),
        })

    # 东财价格单位核对：与同花顺 latest 对齐
    for row in rows:
        t = ths.get(row["code"])
        if t and abs(float(t["latest"]) - row["price"]) > 0.011:
            raise AssertionError(f"{row['name']} 价格不一致 {row['price']} vs {t['latest']}")

    rows.sort(key=lambda x: x["sealAmount"], reverse=True)
    head = rows[:15]                                        # 封单额前 15
    tall = [r for r in rows if r["boards"] >= 3]            # 全部 3 板以上
    picked, seen = [], set()
    for r in head + tall:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        picked.append(r)

    max_b = max((r["boards"] for r in rows), default=1)
    print(f"  涨停 {tc} 家，最高 {max_b} 板，3 板以上 {len(tall)} 家")
    print(f"  取样 {len(picked)} 只（封单前 15 + 3 板以上）")
    for r in picked[:5]:
        print(f"    {r['name']:<6} {r['boards']}板 封单 {r['sealAmount'] / 1e8:.2f}亿 "
              f"{r['boardType']} {r['reason'][:28]}")
    return tc, picked


# ============================================================
# 4. 跌停池
# ============================================================
def fetch_dt():
    url = (f"{EX}/getTopicDTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
           f"&Pageindex=0&pagesize=100&sort=fund%3Aasc&date={TRADE_DATE.replace('-', '')}")
    d = get_json(url, referer="https://quote.eastmoney.com/")["data"]
    tc = d["tc"]
    rows = []
    for r in d["pool"][:10]:
        code = r["c"]
        hybk = r.get("hybk") or ""
        lbt = hhmmss(r.get("lbt"))
        rows.append({
            "code": code,
            "setcode": setcode_of(code),
            "name": r["n"],
            "price": round(r["p"] / 1000, 2),
            "pct": round(float(r["zdp"]), 2),
            "days": int(r.get("days") or 1),
            "boardType": "换手板" if int(r.get("days") or 1) == 1 else "连续跌停",
            "sealAmount": int(r.get("fund") or 0),
            "openTimes": int(r.get("oc") or 0),
            "theme": hybk,
            "detail": " · ".join(x for x in (hybk, f"最后封板 {lbt}") if x),
        })
    print(f"  东财跌停池 total={tc} 条")
    # 全部代码（不只展示的 10 行），供与同花顺逐只比对：北交所是同花顺 filter 的盲区
    all_codes = {r["c"] for r in d["pool"]}
    return tc, rows, all_codes


def cross_check_dt(tc, em_codes):
    url = (f"{THS}/dataapi/limit_up/lower_limit_pool?page=1&limit=100"
           "&field=199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
           "1968584,3475914,9003,9004"
           "&filter=HS,GEM2STAR&order_field=330324&order_type=0"
           f"&date={TRADE_DATE.replace('-', '')}")
    d = get_json(url, referer="https://data.10jqka.com.cn/")["data"]
    n = d["page"]["total"]
    print(f"  同花顺跌停池 total={n} 条")
    # 同花顺 limit 上限 200，跌停超 200 家时返回不全，此时只核对「东财的前 200 家都在同花顺里」
    ths = {r["code"] for r in d["info"]}
    only_em = set(em_codes) - ths
    bj = {c for c in only_em if c.startswith(("4", "8", "9"))}
    if only_em:
        print(f"  注：{len(only_em)} 只仅东财有（{sorted(only_em)}），其中北交所 {len(bj)} 只")
    assert not (only_em - bj), f"沪深跌停家数不一致：仅东财有 {sorted(only_em - bj)}"
    assert n + len(bj) == tc, f"跌停家数两源不一致：东财 {tc} vs 同花顺 {n}+北交所{len(bj)}"


# ============================================================
# 5. 席位活跃度
# ============================================================
def normalize_seat(name):
    n = name.replace("股份有限公司", "").replace("有限公司", "")
    n = n.replace("证券营业部", "").replace("证券营业", "").replace("营业部", "")
    return n.strip()


# 已知游资席位（与 fetch_seat_stocks.py 的 PROFILES.aliases 一致，作用于 normalize_seat 之后的名字）
PRIORITY_SEAT = [r"国泰海通.*江苏路", r"国泰君安.*江苏路", r"国盛证券.*桑田路",
                 r"中信证券.*溧阳路", r"华鑫证券.*上海分公司", r"中国银河.*绍兴鲁迅中路"]


def fetch_seats():
    def fetch_detail(report):
        out, page, pages = [], 1, None
        while True:
            filt = urllib.parse.quote(f"(TRADE_DATE='{TRADE_DATE}')")
            url = (f"{DATA}/api/data/v1/get?reportName={report}&columns=ALL"
                   f"&filter={filt}&pageNumber={page}&pageSize=500&source=WEB&client=WEB")
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
    buy_cnt, sell_cnt = {}, {}
    for r in buys:
        k = r.get("OPERATEDEPT_CODE") or r["OPERATEDEPT_NAME"]
        a = agg.setdefault(k, {"name": r["OPERATEDEPT_NAME"], "buy": 0.0, "sell": 0.0})
        a["buy"] += float(r["BUY"] or 0)
        stocks.setdefault(k, set()).add(r["SECURITY_CODE"])
        buy_cnt[k] = buy_cnt.get(k, 0) + 1
    for r in sells:
        k = r.get("OPERATEDEPT_CODE") or r["OPERATEDEPT_NAME"]
        a = agg.setdefault(k, {"name": r["OPERATEDEPT_NAME"], "buy": 0.0, "sell": 0.0})
        a["sell"] += float(r["SELL"] or 0)
        stocks.setdefault(k, set()).add(r["SECURITY_CODE"])
        sell_cnt[k] = sell_cnt.get(k, 0) + 1

    seats = []
    for k, a in agg.items():
        seats.append({
            "seat": normalize_seat(a["name"]),
            "turnover": round((a["buy"] + a["sell"]) / 1e4, 2),
            "lists": len(stocks.get(k, ())),
            "buyCount": buy_cnt.get(k, 0),
            "buyAmount": round(a["buy"] / 1e4, 2),
            "sellCount": sell_cnt.get(k, 0),
            "sellAmount": round(a["sell"] / 1e4, 2),
            "net": round((a["buy"] - a["sell"]) / 1e4, 2),
        })
    print(f"  聚合出 {len(seats)} 个席位")

    seats.sort(key=lambda s: s["net"], reverse=True)
    picked, used = [], set()
    MAX_SEATS = 14

    def take(s):
        if s["seat"] in used or len(picked) >= MAX_SEATS:
            return
        used.add(s["seat"])
        picked.append(s)

    # ① 已知游资席位优先入样。
    # 只按净买额取样时，游资席位经常被挤到 10 名开外，龙虎榜「关联游资」列就会整列空白
    # （2026-11-18 实测：当日 10 个取样席位里一个游资席位都没有，但章盟主/桑田路当天确实上榜）。
    hot = 0
    for s in seats:
        if hot >= 5:
            break
        if any(re.search(p, s["seat"]) for p in PRIORITY_SEAT):
            take(s)
            hot += 1
    # ② 净买前 5 / 净卖末 3 / 机构与股通
    for s in seats[:5]:
        take(s)
    for s in seats[-3:]:
        take(s)
    for kw in ("机构专用", "沪股通", "深股通"):
        for s in seats:
            if kw in s["seat"]:
                take(s)
                break
    for s in seats:
        take(s)

    picked.sort(key=lambda s: s["net"], reverse=True)
    print(f"  取样 {len(picked)} 个席位，净买额区间 "
          f"{picked[0]['net'] / 1e4:+.2f} 亿 ~ {picked[-1]['net'] / 1e4:+.2f} 亿")
    return picked


# ============================================================
# 6. 个股活跃度
# ============================================================
def fetch_stock_activity():
    filt = urllib.parse.quote(f"(TRADE_DATE='{TRADE_DATE}')")
    url = (f"{DATA}/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW"
           f"&columns=ALL&filter={filt}&pageNumber=1&pageSize=200&source=WEB&client=WEB")
    rows = get_json(url, referer="https://data.eastmoney.com/")["result"]["data"]
    print(f"  龙虎榜个股明细 {len(rows)} 条")

    agg = {}
    for r in rows:
        code = r["SECURITY_CODE"]
        setcode = {"SH": "1", "SZ": "0", "BJ": "2"}.get(r.get("MARKET", ""), "1")
        a = agg.setdefault(code, {
            "code": code, "setcode": setcode, "name": r["SECURITY_NAME_ABBR"],
            "lists": 0, "_buy": 0.0, "_sell": 0.0, "lastDate": r["TRADE_DATE"][:10],
        })
        a["lists"] += 1
        a["_buy"] += float(r["BILLBOARD_BUY_AMT"] or 0)
        a["_sell"] += float(r["BILLBOARD_SELL_AMT"] or 0)

    out = []
    for a in agg.values():
        out.append({
            "code": a["code"], "setcode": a["setcode"], "name": a["name"],
            "lists": a["lists"], "lastDate": a["lastDate"],
            "buyAmount": round(a["_buy"] / 1e4, 2),
            "sellAmount": round(a["_sell"] / 1e4, 2),
        })
    out.sort(key=lambda x: (x["lists"], x["buyAmount"]), reverse=True)
    out = out[:10]
    print(f"  聚合 {len(agg)} 只股票，取上榜次数前 10（首位 {out[0]['name']} {out[0]['lists']} 次）")
    return out


# ============================================================
# 7. 观察池 + 60 日 K 线
# ============================================================
WATCH = [
    ("600241", "1", "时代万恒", "电池"),
    ("002733", "0", "雄韬股份", "电池"),
    ("300821", "0", "东岳硅材", "化学制品"),
    ("300413", "0", "芒果超媒", "文化传媒"),
    ("605577", "1", "龙版传媒", "文化传媒"),
]


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
    """日线前复权，只取 **TRADE_DATE 及之前** 的 n 根。

    腾讯 qfqday 每行 = [日期, 开, 收, 高, 低, 成交量(手)]，取日期与收盘。
    两家前复权都以最新价为基准，日常收盘价一致；除权处理细节可能差几分钱，
    仅影响历史曲线形状，不影响「今日收盘 / 区间涨幅」这类结论。

    ⚠️ 两个必须处理的地方：
    1) 盘中跑脚本时，腾讯会带上**当日未收盘**的半根 K（11-19 跑 11-18 的数据时会多出 1 根），
       不截断就会出现 61 根、且末根是盘中价，区间涨幅全错。
    2) 停牌/新股根数不足时补齐是错的 —— 宁可少一根，也不能插值造数据。
    """
    limit = TRADE_DATE.replace("-", "")

    def clip(rows):
        rows = [r for r in rows if r["d"] <= limit]
        return rows[-n:]

    try:
        url = (f"{HIS}/api/qt/stock/kline/get?secid={em_secid(code, setcode)}"
               "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1"
               f"&end={limit}&lmt={n}")
        d = get_json(url, retries=1)["data"]
        if d and d.get("klines"):
            rows = clip([{"d": ln.split(",")[0].replace("-", ""),
                          "c": round(float(ln.split(",")[2]), 2)} for ln in d["klines"]])
            if rows:
                return rows
    except Exception:                              # noqa: BLE001
        pass
    mkt = {"1": "sh", "0": "sz", "2": "bj"}.get(setcode, "sh")
    d = get_json(f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                 f"?param={mkt}{code},day,,,{n + 5},qfq", referer="https://gu.qq.com/")["data"]
    node = d.get(f"{mkt}{code}") or {}
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise RuntimeError(f"{code} K 线为空（东财+腾讯均无）")
    return clip([{"d": r[0].replace("-", ""), "c": round(float(r[2]), 2)} for r in rows])


def fetch_watchlist():
    out = []
    for code, setcode, name, industry in WATCH:
        q = fetch_quote(code, setcode)
        kl = fetch_kline(code, setcode, 60)
        assert kl, f"{code} K 线为空"
        if len(kl) < 60:
            print(f"  ⚠️ {name}({code}) 只有 {len(kl)} 根 K 线（停牌/次新），按实际根数保留")
        assert kl[-1]["d"] == TRADE_DATE.replace("-", ""), \
            f"{code} 末根 K 线 {kl[-1]['d']} != {TRADE_DATE}"
        out.append({
            "code": code, "setcode": setcode, "name": name, "industry": industry,
            "price": q["price"], "pct": q["pct"], "turnoverRate": q["turnoverRate"],
            "kline60": kl,
        })
        rng = (kl[-1]["c"] / kl[0]["c"] - 1) * 100
        print(f"  {code} {name}: {len(kl)} 根 {kl[0]['d']}→{kl[-1]['d']} "
              f"{kl[0]['c']}→{kl[-1]['c']} 区间 {rng:+.2f}% 现价 {q['price']} ({q['pct']:+.2f}%)")
        time.sleep(0.4)
    return out


# ============================================================
# main
# ============================================================
def main():
    print("[0/6] 指数（东财）+ 新浪二次核对")
    indices = cached("indices", fetch_indices)
    cross_check_index(indices)

    print("\n[1/6] 行业板块")
    _b = cached("boards", lambda: list(fetch_boards()))
    boards, board_stats, boards_all = _b[0], _b[1], _b[2]

    print("\n[2/6] 涨停池（东财数值 + 同花顺原因）")
    _lu = cached("limitup", lambda: list(build_limit_up()))
    lu_total, lu_pool = _lu[0], _lu[1]

    print("\n[3/6] 跌停池 + 同花顺核对")
    _ld = cached("limitdown", lambda: list(fetch_dt()))
    ld_total, ld_pool, ld_codes = _ld[0], _ld[1], _ld[2]
    cross_check_dt(ld_total, ld_codes)

    print("\n[4/6] 席位活跃度")
    seats = cached("seats", fetch_seats)

    print("\n[5/6] 个股活跃度")
    stock_act = cached("stockact", fetch_stock_activity)

    print("\n[6/6] 观察池 K 线")
    watch = cached("watch", fetch_watchlist)

    # 每日本地存档：全量 496 个行业单独落一份，轮动分析要用它（快照里只有 27 个取样）
    import os
    day_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "daily", TRADE_DATE.replace("-", ""))
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, "industry_full.json"), "w", encoding="utf-8") as f:
        json.dump({"asOf": TRADE_DATE, "count": len(boards_all),
                   "note": "东财 496 个二级行业全量（按涨幅降序），供行业轮动对比",
                   "items": boards_all}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  全量行业 {len(boards_all)} 个 -> data/daily/{TRADE_DATE.replace('-', '')}/industry_full.json")

    snap = {
        "tradeDate": TRADE_DATE,
        "capturedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "东方财富 + 同花顺（通达信配额未恢复）",
        "sourceNote": ("通达信 MCP 配额未恢复，本轮全部改用公开接口：指数/涨跌家数取自东财 "
                       "ulist 并经新浪财经核对；板块为东财 496 个二级行业（与 11-06 同口径，"
                       "与 10 月通达信口径不可比）；涨停家数与跌停家数经东财、同花顺双源核对一致；"
                       "涨停原因与板型（一字/T字/换手）取自同花顺；龙虎榜与观察池 K 线取自东财。"),
        "indices": indices,
        "industryBoards": boards,
        "industryBoardStats": board_stats,
        "limitUpPool": lu_pool,
        "limitUpTotal": lu_total,
        "limitDownPool": ld_pool,
        "limitDownTotal": ld_total,
        "seatActivity": seats,
        "stockListActivity": stock_act,
        "watchlist": watch,
    }

    assert len(snap["indices"]) == 3
    assert len(snap["industryBoards"]) == 27
    assert len(snap["seatActivity"]) >= 10, f"席位 {len(snap['seatActivity'])} 条"
    assert len(snap["stockListActivity"]) == 10
    assert len(snap["watchlist"]) == 5
    assert all(len(w["kline60"]) == 60 for w in snap["watchlist"])
    assert snap["limitUpTotal"] >= len(snap["limitUpPool"])
    assert snap["limitDownTotal"] >= len(snap["limitDownPool"])

    with open("raw_snapshot.json", "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)

    adv = indices[0]["advancers"] + indices[1]["advancers"]
    dec = indices[0]["decliners"] + indices[1]["decliners"]
    print(f"\nOK -> raw_snapshot.json")
    print(f"tradeDate={TRADE_DATE} 涨停={lu_total} 跌停={ld_total} "
          f"涨{adv}/跌{dec} 成交={(indices[0]['amount'] + indices[1]['amount']) / 1e8:.0f}亿")


if __name__ == "__main__":
    main()
