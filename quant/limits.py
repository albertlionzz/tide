# -*- coding: utf-8 -*-
"""
妖股量化 · 特征层 · 涨停与连板重算

为什么不抓历史涨停池：东财 getTopicZTPool 只保留约 1 个月（实测 10-30 有、10-15 起为 0），
无法支撑多年妖股研究。改为用日线自己重算，口径统一、可回溯。

判定规则（三种口径互为校验，全部满足才算涨停）：

    ① 涨幅阈值（不用「收盘价 = 昨收×(1+限幅)」的精确算式）
       主板 9.8% / 创业板·科创板 19.8% / 北交所 29.8% / 主板 ST 4.8%
       原因：我们取的是**前复权**价（fqt=1），复权后的价格比例会被微小改写，
       精确算涨停价再做四舍五入比对，在除权日附近会漏判。按涨幅判稳得多。
    ② 涨幅上限 thr+2.0 个百分点
       排除新股上市首日（主板 +44%）与创业板/科创前 5 日无涨跌幅限制的情形，
       否则这些「暴涨日」会被误判成涨停，污染「全市场涨停家数」。
    ③ 收盘价 = 当日最高价（跌停则 = 最低价）
       涨停必然收在最高价，这条能剔掉「涨幅接近 10% 但没封板」的票。

⚠️ 已知局限：ST 的历史状态不可得，只能用**当前**名称里的 ST 标记近似。
   主板 ST 股按 5% 判、创业板/科创板 ST 按 20% 判（注册制板块 ST 不降限幅）。
   妖股极少是 ST，主要影响「全市场涨停家数」这个情绪特征，方向是略偏低。
"""

from __future__ import annotations

LIMIT_THR = {"main": 9.8, "chinext": 19.8, "star": 19.8, "bj": 29.8}
ST_THR = 4.8                  # 仅主板 ST
THR_TOL = 2.0                 # 涨幅上限容忍（排除新股无涨跌幅日）


def thr_of(board: str, st: bool = False) -> float:
    if st and board == "main":
        return ST_THR
    return LIMIT_THR.get(board, 9.8)


def is_limit_up(pct: float | None, close: float, high: float,
                board: str, st: bool = False) -> bool:
    if pct is None:
        return False
    thr = thr_of(board, st)
    if not (thr <= pct <= thr + THR_TOL):
        return False
    return close >= high - 0.005


def is_limit_down(pct: float | None, close: float, low: float,
                  board: str, st: bool = False) -> bool:
    if pct is None:
        return False
    thr = thr_of(board, st)
    if not (-(thr + THR_TOL) <= pct <= -thr):
        return False
    return close <= low + 0.005


def annotate(rows: list[list[str]], board: str, st: bool = False) -> list[dict]:
    """原始 kline 行（f51..f61 字符串）→ 带涨停/连板标注的字典序列。

    每项：d 日期 / o h l c 价 / v 量(手) / a 额(元) / pct 涨跌幅% / turn 换手%
          lu 涨停 / ld 跌停 / lb 连板数 / gap 开盘缺口% / seal 一字板
    """
    out: list[dict] = []
    prev_close: float | None = None
    lb = 0
    for r in rows:
        try:
            d = r[0]
            o, c, h, l = float(r[1]), float(r[2]), float(r[3]), float(r[4])
            v, a = float(r[5]), float(r[6])
            pct = float(r[8]) if len(r) > 8 and r[8] not in ("", "-") else None
            turn = float(r[10]) if len(r) > 10 and r[10] not in ("", "-") else None
        except (ValueError, IndexError):
            continue
        if prev_close and pct is None:
            pct = round((c / prev_close - 1) * 100, 2)

        lu = is_limit_up(pct, c, h, board, st)
        ld = is_limit_down(pct, c, l, board, st)
        gap = round((o / prev_close - 1) * 100, 2) if prev_close else None
        seal = lu and abs(o - c) < 0.005 and abs(h - c) < 0.005

        lb = lb + 1 if lu else 0
        out.append({"d": d, "o": o, "h": h, "l": l, "c": c, "v": v, "a": a,
                    "pct": pct if pct is not None else 0.0, "turn": turn,
                    "lu": lu, "ld": ld, "lb": lb, "gap": gap, "seal": seal})
        prev_close = c
    return out


def pct_over(series: list[dict], i: int, n: int) -> float | None:
    """第 i 根相对 n 根前的涨幅（%）。"""
    if i - n < 0:
        return None
    base = series[i - n]["c"]
    return round((series[i]["c"] / base - 1) * 100, 2) if base else None
