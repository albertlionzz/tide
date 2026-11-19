# -*- coding: utf-8 -*-
"""
潮汐 · 每日本地归档
====================
把当日快照落到 `data/daily/<YYYYMMDD>/`，供日后回溯与逐日对比。

为什么必须归档：
  raw_snapshot.json 只有一个，下一次更新就被覆盖。11-09 那轮就是没归档，
  导致趋势图永久缺一天，事后补不回来（板块接口只给当日）。
  归档是廉价的保险：一天约 120KB（快照 + 加工结果），一年 30MB 以内。

产出：
  data/daily/<YYYYMMDD>/raw_snapshot.json   接口原始快照（可重算任何指标）
  data/daily/<YYYYMMDD>/tide_data.json      当日加工结果
  data/daily/index.json                     索引：每交易日关键读数，供快速查询
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY = os.path.join(HERE, "data", "daily")


def load_tide(raw, date):
    """当日加工结果。根目录 tide_data.json 若不是这天的，就用当前 derive 重算 ——
    口径统一（derive 改过公式，旧 tide_data 不能直接抄）。"""
    p = os.path.join(HERE, "tide_data.json")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as fh:
            td = json.load(fh)
        if (td.get("meta") or {}).get("tradeDate") == date:
            return td
    sys.path.insert(0, HERE)
    import derive                                  # noqa: E402
    return derive.build(raw)


def main():
    ap = argparse.ArgumentParser(description="按交易日归档到 data/daily/<YYYYMMDD>/")
    ap.add_argument("--snapshot", default=os.path.join(HERE, "raw_snapshot.json"),
                    help="要归档的快照，默认当日 raw_snapshot.json；补档可用 raw_snapshot_MMDD.json")
    args = ap.parse_args()
    with open(args.snapshot, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    date = raw["tradeDate"]
    day = date.replace("-", "")
    ddir = os.path.join(DAILY, day)
    os.makedirs(ddir, exist_ok=True)

    td = load_tide(raw, date)

    # 快照统一落成 raw_snapshot.json（补档时源文件名带 MMDD，内容同构）
    is_current = os.path.basename(args.snapshot) == "raw_snapshot.json"
    kept = []

    def dump(name, content):
        with open(os.path.join(ddir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
        kept.append(name)

    dump("raw_snapshot.json", json.dumps(raw, ensure_ascii=False))
    dump("tide_data.json", json.dumps(td, ensure_ascii=False))

    # delta / top5 只属于「当前这次运行」；补档时不拷，否则会把今天的对比塞进历史目录
    if is_current:
        dp = os.path.join(HERE, "delta.json")
        if os.path.exists(dp):
            try:
                with open(dp, "r", encoding="utf-8") as fh:
                    dj = json.load(fh)
                if dj.get("asOf") == date:
                    dump("delta.json", json.dumps(dj, ensure_ascii=False))
            except ValueError:
                pass
        tp = os.path.join(HERE, "top5.json")
        if os.path.exists(tp):
            with open(tp, "r", encoding="utf-8") as fh:
                dump("top5.json", fh.read())

    row = {
        "date": date,
        "fear": td["fear"]["value"],
        "label": td["fear"]["label"],
        "sh": next(i["price"] for i in td["indices"] if i["name"] == "上证指数"),
        "shPct": next(i["pct"] for i in td["indices"] if i["name"] == "上证指数"),
        "up": td["limit"]["upTotal"],
        "down": td["limit"]["downTotal"],
        "maxBoards": td["limit"]["maxBoards"],
        "adv": td["breadth"]["advancers"],
        "dec": td["breadth"]["decliners"],
        "turnoverYi": round(td["turnoverYi"], 0),
        "boardUp": td["boardStats"]["up"],
        "boardDown": td["boardStats"]["down"],
        "boardTotal": td["boardStats"].get("total"),
        "files": kept,
    }

    ipath = os.path.join(DAILY, "index.json")
    idx = {"note": "每个交易日一行；由 archive_daily.py 生成，可重复运行。", "days": []}
    if os.path.exists(ipath):
        with open(ipath, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        idx["days"] = [d for d in old.get("days", []) if d.get("date") != date]
    idx["days"].append(row)
    idx["days"].sort(key=lambda d: d["date"])
    idx["count"] = len(idx["days"])
    with open(ipath, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)

    print(f"归档 {date} → data/daily/{day}/（{', '.join(kept)}）")
    print(f"索引共 {idx['count']} 个交易日："
          f"{idx['days'][0]['date']} ~ {idx['days'][-1]['date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
