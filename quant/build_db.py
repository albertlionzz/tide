# -*- coding: utf-8 -*-
"""
妖股量化 · 数据层 · 合并成 SQLite

把 data/raw/<code>.json.gz 逐只读出（用 limits.annotate 重算涨停/连板），
写入 data/market.db。

表 bars：code/name/board/date/o/h/l/c/v/a/pct/turn/lu/ld/lb/gap/seal
索引：主键(code,date)、idx_date(date)

为什么用 SQLite 而不是 parquet：本机装不上 pyarrow（pip 报 No matching distribution），
标准库 sqlite3 零依赖，且能按日期切片查询，回测够用。
"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import time

import limits

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
DB = os.path.join(HERE, "data", "market.db")
UNIVERSE = os.path.join(HERE, "data", "universe.json")

SCHEMA = """
DROP TABLE IF EXISTS bars;
CREATE TABLE bars(
    code TEXT NOT NULL, name TEXT, board TEXT, date TEXT NOT NULL,
    o REAL, h REAL, l REAL, c REAL, v REAL, a REAL,
    pct REAL, turn REAL,
    lu INTEGER, ld INTEGER, lb INTEGER, gap REAL, seal INTEGER,
    PRIMARY KEY(code, date)
);
"""


def board_of(code: str) -> str:
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("4", "8", "9")):
        return "bj"
    return "main"


def main() -> int:
    meta = {}
    if os.path.exists(UNIVERSE):
        with open(UNIVERSE, "r", encoding="utf-8") as fh:
            meta = {x["code"]: x for x in json.load(fh)["items"]}

    files = sorted(f for f in os.listdir(RAW) if f.endswith(".json.gz"))
    print(f"待合并 {len(files)} 只")

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=OFF")
    cur.executescript(SCHEMA)

    total, skipped = 0, 0
    t0 = time.time()
    buf = []
    for i, f in enumerate(files, 1):
        code = f.split(".")[0]
        try:
            with gzip.open(os.path.join(RAW, f), "rt", encoding="utf-8") as fh:
                obj = json.load(fh)
            rows = limits.annotate(obj["rows"], board_of(code),
                                   bool((meta.get(code) or {}).get("st")))
        except Exception as e:                        # noqa: BLE001
            print(f"  [!] {code} 跳过：{type(e).__name__}: {e}")
            skipped += 1
            continue
        name = obj.get("name") or (meta.get(code) or {}).get("name", "")
        board = board_of(code)
        for r in rows:
            buf.append((code, name, board, r["d"], r["o"], r["h"], r["l"], r["c"],
                        r["v"], r["a"], r["pct"], r["turn"],
                        1 if r["lu"] else 0, 1 if r["ld"] else 0, r["lb"],
                        r["gap"], 1 if r["seal"] else 0))
        total += len(rows)
        if len(buf) >= 20000:
            cur.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
            buf.clear()
        if i % 500 == 0:
            print(f"  {i}/{len(files)}  行数 {total}  {time.time() - t0:.0f}s", flush=True)

    if buf:
        cur.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buf)
    cur.execute("CREATE INDEX idx_date ON bars(date)")
    cur.execute("CREATE INDEX idx_code_date ON bars(code, date)")
    con.commit()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT code), MIN(date), MAX(date) FROM bars")
    cnt, codes, dmin, dmax = cur.fetchone()
    cur.execute("""CREATE TABLE IF NOT EXISTS meta(
        k TEXT PRIMARY KEY, v TEXT)""")
    for k, v in [("builtAt", time.strftime("%Y-%m-%d %H:%M:%S")),
                 ("rows", str(cnt)), ("codes", str(codes)),
                 ("span", f"{dmin}~{dmax}"), ("skipped", str(skipped))]:
        cur.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))
    con.commit()
    con.close()

    print(f"OK -> {DB}")
    print(f"  {cnt} 行 / {codes} 只 / {dmin}~{dmax} / 跳过 {skipped} / 耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
