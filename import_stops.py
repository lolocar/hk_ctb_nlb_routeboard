#!/usr/bin/env python3
"""站点数据导入 CLI。

用法:
    python import_stops.py            # 导入全部（CTB + NLB）
    python import_stops.py ctb        # 仅城巴
    python import_stops.py nlb        # 仅新巴

前置: 需先运行 import_routes.py 建立 route 表（站点导入依赖路线列表）。
注意: 城巴需对每条路线 x 2 方向 + 每个去重站点各发请求，耗时较长（数百次请求）。
"""
from __future__ import annotations

import sys

from db import get_conn, replace_operator_stops
from importers.ctb_stops import import_ctb_stops
from importers.nlb_stops import import_nlb_stops


def main(argv: list[str]) -> int:
    targets = argv[1:] or ["ctb", "nlb"]
    conn = get_conn()
    try:
        for t in targets:
            t = t.lower()
            if t == "ctb":
                replace_operator_stops(conn, "CTB")
                n_stops, n_links = import_ctb_stops(conn)
                conn.commit()
                print(f"CTB: imported {n_stops} stops, {n_links} route-stop links")
            elif t == "nlb":
                replace_operator_stops(conn, "NLB")
                n_stops, n_links = import_nlb_stops(conn)
                conn.commit()
                print(f"NLB: imported {n_stops} stops, {n_links} route-stop links")
            else:
                print(f"unknown target: {t} (use 'ctb' or 'nlb')", file=sys.stderr)
                return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
