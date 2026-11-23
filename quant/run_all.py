# -*- coding: utf-8 -*-
"""
妖股量化 · 一键全流程

顺序有依赖，不要跳步：
    universe → fetch_lhb → fetch_daily → lhb → dataset → enrich → evolve → report

    build_db.py 与 features.py 是早期落 SQLite/宽表的版本，现已由 dataset.py
    的两遍扫描取代（宽表 780 万行内存吃不消），保留仅作参考，不进主流程。

日常增量：
    python run_all.py --step daily     # 只补日线（已有文件跳过）
    python run_all.py --step rebuild   # 数据齐了之后重建样本+进化+报告
    python run_all.py                  # 全跑
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

STEPS = {
    "universe": ["universe.py"],
    "lhb_fetch": ["fetch_lhb.py"],
    "daily": ["fetch_daily.py"],
    "lhb": ["lhb.py"],
    "dataset": ["dataset.py"],
    "enrich": ["enrich.py"],
    "evolve": ["evolve.py"],
    "report": ["report.py"],
}


def run(name: str, extra: list[str]) -> bool:
    cmd = [PY, "-u"] + STEPS[name] + extra
    print(f"\n=== {name} ===\n{' '.join(cmd)}", flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=HERE)
    ok = r.returncode == 0
    print(f"--- {name} {'OK' if ok else 'FAIL'} {time.time() - t0:.0f}s", flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", default="all",
                    choices=["all", "daily", "rebuild", "data"])
    ap.add_argument("--iters", type=int, default=0, help="覆盖进化迭代数")
    args = ap.parse_args()

    extra_evolve = ["--iters", str(args.iters or 220)]

    if args.step == "all":
        plan = ["universe", "lhb_fetch", "daily", "lhb", "dataset", "enrich",
                "evolve", "report"]
    elif args.step == "data":
        plan = ["universe", "lhb_fetch", "daily", "lhb", "dataset", "enrich"]
    elif args.step == "daily":
        plan = ["daily"]
    else:                                    # rebuild
        plan = ["lhb", "dataset", "enrich", "evolve", "report"]

    for s in plan:
        if not run(s, extra_evolve if s == "evolve" else []):
            print(f"!! {s} 失败，中止")
            return 1
    print("\n全部完成。报告见 quant/report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
