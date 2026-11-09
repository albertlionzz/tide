# -*- coding: utf-8 -*-
"""
潮汐 · 历史序列构建
===================
把历次快照（raw_snapshot_YYYYMMDD.json）**用当前 derive.py 重新算一遍**，
得到口径一致的历史序列，供前端画趋势图。

为什么必须重算：
  derive.py 在 2026-10-21 修过一个反向映射 bug（scale() 的 clamp 把 100/0
  顺序传反，导致连板高度、赚钱效应两个反向分量被钉死在极值）。修复前后的
  恐慌指数不可直接比较。因此历史点不能抄旧 tide_data.json，必须用同一版
  公式重算。

收录字段（跨日可比）：
  date / fear / label / limitUp / limitDown / maxBoards / turnoverYi /
  advancers / decliners / advRatio / amplitude

板块统计不入库：10 月与 11 月的板块分类口径不同（通达信 ~100 个 vs 东财
496 个二级行业），跨日比较没有意义。

输出：history.json
"""

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import derive          # noqa: E402  复用同一套恐慌指数公式，避免口径漂移


def main():
    # 只收 raw_snapshot_MMDD.json（4 位日期后缀），分片/备份文件不参与
    files = sorted(
        f for f in glob.glob(os.path.join(HERE, "raw_snapshot_*.json"))
        if re.fullmatch(r"raw_snapshot_\d{4}\.json", os.path.basename(f))
    )
    # 当日快照单独追加（它就是当前交易日的原始数据）
    cur = os.path.join(HERE, "raw_snapshot.json")

    points, seen = [], set()
    for path in files + [cur]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            d = derive.build(raw)
        except Exception as e:                      # noqa: BLE001
            print(f"  [!] {os.path.basename(path)} 跳过：{e}")
            continue
        date = d["meta"]["tradeDate"]
        if date in seen:
            continue
        seen.add(date)
        points.append({
            "date": date,
            "fear": d["fear"]["value"],
            "label": d["fear"]["label"],
            "limitUp": d["limit"]["upTotal"],
            "limitDown": d["limit"]["downTotal"],
            "maxBoards": d["limit"]["maxBoards"],
            "turnoverYi": round(d["turnoverYi"], 0),
            "advancers": d["breadth"]["advancers"],
            "decliners": d["breadth"]["decliners"],
            "advRatio": d["breadth"]["advRatio"],
            "amplitude": d["amplitude"],
            "parts": [p["value"] for p in d["fear"]["parts"]],
            "source": d["meta"]["source"],
        })

    points.sort(key=lambda p: p["date"])
    out = {
        "meta": {
            "generatedBy": "build_history.py",
            "note": "全部点用当前 derive.py 重算，口径一致；板块分类跨月不同源，故不入库。",
            "count": len(points),
            "span": [points[0]["date"], points[-1]["date"]] if points else [],
        },
        "points": points,
    }
    with open(os.path.join(HERE, "history.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print(f"history.json 写入 {len(points)} 个交易日")
    for p in points:
        print(f"  {p['date']}  恐慌 {p['fear']:>3} {p['label']:<3} "
              f"涨停 {p['limitUp']:>3} 跌停 {p['limitDown']:>2} "
              f"成交 {p['turnoverYi']:>6.0f} 亿  涨跌 {p['advancers']}/{p['decliners']}")

    # 自检：当日点必须与 tide_data.json 完全一致（同一份公式）
    tp = os.path.join(HERE, "tide_data.json")
    if os.path.exists(tp):
        with open(tp, "r", encoding="utf-8") as fh:
            today = json.load(fh)
        cur_point = points[-1]
        assert cur_point["date"] == today["meta"]["tradeDate"], "最新历史点不是当日"
        assert cur_point["fear"] == today["fear"]["value"], \
            f"口径漂移：history {cur_point['fear']} vs tide_data {today['fear']['value']}"
        print("  自检通过：最新点与 tide_data.json 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
