#!/usr/bin/env python3
"""路线数据导入 CLI。

用法:
    python import_routes.py            # 导入全部（CTB + NLB）
    python import_routes.py ctb        # 仅城巴
    python import_routes.py nlb        # 仅新巴
"""
from __future__ import annotations

import sys

from db import get_conn, replace_operator_routes
from importers.ctb import import_ctb_routes
from importers.nlb import import_nlb_routes


def main(argv: list[str]) -> int:
    targets = argv[1:] or ["ctb", "nlb"]
    conn = get_conn()
    try:
        for t in targets:
            t = t.lower()
            if t == "ctb":
                replace_operator_routes(conn, "CTB")
                n = import_ctb_routes(conn)
                print(f"CTB: imported {n} routes")
            elif t == "nlb":
                replace_operator_routes(conn, "NLB")
                n = import_nlb_routes(conn)
                print(f"NLB: imported {n} routes")
            else:
                print(f"unknown target: {t} (use 'ctb' or 'nlb')", file=sys.stderr)
                return 1
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
