#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从研究底稿 xlsx 中抽取科技赛道筛选池 -> screen_pool.json

用标准库直接解 xlsx（不依赖 openpyxl），兼容 inline string 与 shared string。
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
SRC = Path(
    r"C:/Users/zhouy/XiaomiMiMoProjects/.mimo-sessions/2026-09-29/"
    r"帮我做一个股票分析网站，采用苹果极简风格，包含每天行业板块分析，个股分析，龙虎榜/"
    r"research/stock-screen/股票筛选_科技赛道_2026-09-29.xlsx"
)
OUT = HERE / "screen_pool.json"

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}


def col_index(ref: str) -> int:
    """A1 -> 0, B1 -> 1, AA1 -> 26"""
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 0
    n = 0
    for ch in letters.group(1):
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    out = []
    for si in root.findall("m:si", NS):
        out.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    return out


def cell_value(cell, shared: list[str]) -> str:
    t = cell.get("t")
    if t == "inlineStr":
        node = cell.find("m:is", NS)
        if node is None:
            return ""
        return "".join(x.text or "" for x in node.iter(f"{{{NS['m']}}}t"))
    if t == "s":
        v = cell.find("m:v", NS)
        if v is None or v.text is None:
            return ""
        idx = int(v.text)
        return shared[idx] if 0 <= idx < len(shared) else ""
    v = cell.find("m:v", NS)
    return v.text if v is not None and v.text is not None else ""


def read_sheet(zf: zipfile.ZipFile, name: str, shared: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read(name))
    rows = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", NS):
            cells[col_index(c.get("r"))] = cell_value(c, shared)
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows


def to_float(v: str) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def to_int(v: str) -> int:
    f = to_float(v)
    return int(f) if f is not None else 0


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"源文件不存在: {SRC}")

    with zipfile.ZipFile(SRC) as zf:
        shared = read_shared_strings(zf)
        sheets = sorted(
            [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")],
            key=lambda s: int(re.search(r"sheet(\d+)", s).group(1)),
        )
        print("工作表:", sheets)

        # 找到「筛选池」主表：表头含 代码 + 名称 + 加分项
        target_rows: list[list[str]] = []
        for s in sheets:
            rows = read_sheet(zf, s, shared)
            if not rows:
                continue
            header = " ".join(str(x) for x in rows[0])
            print(f"  {s}: {len(rows)} 行 | 表头: {header[:110]}")
            if ("代码" in header or "股票代码" in header) and ("名称" in header):
                if len(rows) > len(target_rows):
                    target_rows = rows
                    print(f"    -> 采用 {s} 作为筛选池")

    if not target_rows:
        raise SystemExit("未找到筛选池工作表")

    header = [str(x).strip() for x in target_rows[0]]
    print("\n最终表头:", header)

    def find_col(*names: str) -> int:
        for n in names:
            for i, h in enumerate(header):
                if n in h:
                    return i
        return -1

    c_code = find_col("代码")
    c_name = find_col("名称", "简称")
    c_track = find_col("赛道", "行业", "板块")
    c_mcap = find_col("市值")
    c_turn = find_col("换手")
    # 注意：必须先排除「换手」列，否则「近5日均换手%」会被当成长幅列命中
    c_chg5 = find_col("涨幅", "涨跌幅", "一周")
    c_a1 = find_col("A1", "创始人")
    c_a2 = find_col("A2", "研发")
    c_a3 = find_col("A3", "业务")
    c_a4 = find_col("A4", "游资")
    c_a5 = find_col("A5", "涨幅")
    c_note = find_col("逻辑", "核心", "说明")
    c_risk = find_col("风险")

    print("列定位:", {
        "code": c_code, "name": c_name, "track": c_track, "mcap": c_mcap,
        "turn": c_turn, "chg5": c_chg5, "a1": c_a1, "a2": c_a2,
        "a3": c_a3, "a4": c_a4, "a5": c_a5, "note": c_note, "risk": c_risk,
    })

    def get(row: list[str], i: int) -> str:
        return row[i].strip() if 0 <= i < len(row) and row[i] is not None else ""

    pool = []
    for row in target_rows[1:]:
        code = get(row, c_code)
        if not re.fullmatch(r"\d{6}", code):
            continue
        mcap = to_float(get(row, c_mcap).replace(",", ""))
        turn = to_float(get(row, c_turn))
        chg5 = to_float(get(row, c_chg5).replace("%", "").replace("+", ""))
        pool.append({
            "code": code,
            "name": get(row, c_name),
            "track": get(row, c_track) or "其他",
            "mcap": round(mcap, 1) if mcap is not None else 0,
            "turnover": round(turn, 2) if turn is not None else 0,
            "chg5": round(chg5, 2) if chg5 is not None else 0,
            "a1": to_int(get(row, c_a1)),
            "a2": to_int(get(row, c_a2)),
            "a3": to_int(get(row, c_a3)),
            "a4": to_int(get(row, c_a4)),
            "a5": to_int(get(row, c_a5)),
            "note": get(row, c_note),
            "risk": get(row, c_risk),
        })

    OUT.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n抽取 {len(pool)} 只候选 -> {OUT.name}")

    tracks: dict[str, int] = {}
    for p in pool:
        tracks[p["track"]] = tracks.get(p["track"], 0) + 1
    for t, n in sorted(tracks.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<16} {n}")


if __name__ == "__main__":
    main()
