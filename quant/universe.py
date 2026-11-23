# -*- coding: utf-8 -*-
"""
妖股量化 · 数据层 · 全市场股票池

东财 clist 已被频控封死（http/https/镜像主机全部 RemoteDisconnected），改走新浪
Market_Center 接口拿全 A 股列表。

排序策略：龙虎榜池中的股票排在最前。理由——妖股必然触发龙虎榜披露条件
（连续三日涨幅偏离值累计达 20%），所以「3 年龙虎榜出现过的股票」在数学上
覆盖了全部妖股候选。把它排在前面，即使抓取中途被打断，妖股样本集也是完整的。

口径：
    secid 规则 0.xxxxxx = 深市（含创业板），1.xxxxxx = 沪市（含科创板）
    board 由代码前缀判定，用于涨停幅度阈值（主板 10% / 创业板科创板 20% / ST 5%）

用法：
    python universe.py            # 抓全市场
    python universe.py --check    # 只看现有池子
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "universe.json")
LHB_POOL = os.path.join(DATA, "pool_from_lhb.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}

SINA = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node={node}")
COUNT = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "Market_Center.getHQNodeStockCount?node={node}")

NODES = ["hs_a"]          # 沪深A股；北交所单列不做，30% 涨跌幅与主板不可同模


def board_of(code: str) -> str:
    if code.startswith(("688", "689")):
        return "star"          # 科创板 20%
    if code.startswith(("300", "301")):
        return "chinext"       # 创业板 20%
    if code.startswith(("4", "8", "920")):
        return "bj"            # 北交所 30%（新浪 hs_a 里也混着 920 开头，必须单列）
    return "main"              # 主板 10%


def secid(code: str) -> str:
    """1.=沪市（6 开头 + 科创板），0.=深市。北交所 920 开头归 0.，但本脚本不入池。"""
    return ("1." if code.startswith("6") else "0.") + code


def get(url: str, retry: int = 4) -> str:
    last = None
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            return urllib.request.urlopen(req, timeout=25).read().decode("gbk", "ignore")
        except Exception as e:                     # noqa: BLE001
            last = e
            time.sleep(1.0 + i * 1.2)
    raise RuntimeError(f"请求失败: {last}")


def node_count(node: str) -> int:
    txt = get(COUNT.format(node=node)).strip().strip('"')
    return int(txt)


def fetch_node(node: str) -> list[dict]:
    total = node_count(node)
    pages = (total + 99) // 100
    print(f"  {node}: {total} 只，{pages} 页")
    out = []
    for p in range(1, pages + 1):
        txt = get(SINA.format(page=p, node=node)).strip()
        if not txt or txt in ("null", "[]"):
            break
        try:
            rows = json.loads(txt)
        except json.JSONDecodeError:
            print(f"    第 {p} 页解析失败，跳过")
            continue
        for r in rows:
            code, name = r.get("code", ""), r.get("name", "")
            # 剔除 B 股（900/200）与空值
            if not code or not name or code.startswith(("900", "200")):
                continue
            # nmc = 流通市值（万元），price = 现价 → 反推流通股本（股），
            # 用于给历史日线算换手率代理（股本变动会带来偏差，已在文档标注）
            nmc = r.get("nmc")
            price = r.get("trade")
            shares = None
            try:
                nmc_f, px_f = float(nmc), float(price)
                if nmc_f > 0 and px_f > 0:
                    shares = round(nmc_f * 1e4 / px_f)
            except (TypeError, ValueError):
                pass
            out.append({"code": code, "name": name, "secid": secid(code),
                        "board": board_of(code), "st": "ST" in name.upper(),
                        "nmc": nmc, "shares": shares})
        if p % 15 == 0:
            print(f"    已取 {len(out)} 只", flush=True)
        time.sleep(0.45)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        if not os.path.exists(OUT):
            print("universe.json 不存在")
            return 1
        with open(OUT, "r", encoding="utf-8") as fh:
            o = json.load(fh)
        print(f"共 {o['count']} 只，龙虎榜池前置 {o.get('lhbCount', 0)} 只，{o['byBoard']}")
        print("前 3：", [(x["code"], x["name"]) for x in o["items"][:3]])
        return 0

    os.makedirs(DATA, exist_ok=True)
    items = []
    for node in NODES:
        items.extend(fetch_node(node))

    # 去重（翻页期间排序变动会重复）
    seen, uniq = set(), []
    for x in items:
        if x["code"] in seen:
            continue
        seen.add(x["code"])
        uniq.append(x)

    # 龙虎榜池前置
    lhb = set()
    if os.path.exists(LHB_POOL):
        with open(LHB_POOL, "r", encoding="utf-8") as fh:
            lhb = {x["code"] for x in json.load(fh)["items"]}
    for x in uniq:
        x["inLhb"] = x["code"] in lhb
    uniq.sort(key=lambda x: (not x["inLhb"], x["code"]))

    payload = {"asOf": time.strftime("%Y-%m-%d %H:%M:%S"),
               "count": len(uniq),
               "lhbCount": sum(1 for x in uniq if x["inLhb"]),
               "byBoard": {b: sum(1 for x in uniq if x["board"] == b)
                           for b in ("main", "chinext", "star", "bj")},
               "note": "来源新浪 hs_a；不含北交所（30% 涨跌幅与主板不可同模）；龙虎榜池前置",
               "items": uniq}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"落地 {len(uniq)} 只（龙虎榜池 {payload['lhbCount']} 前置）{payload['byBoard']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
