"""从 DB 导出站点数据到 webapp/stops.json + stops_meta.json（原子替换）。

供手动导出和每日维护（maintenance.py）共用。
- stops.json: 全量站点数据，前端加载用
- stops_meta.json: 轻量元数据（count + updated），前端轮询感知更新
原子替换（写临时文件 + os.replace），避免前端读到半个文件。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

HKT = timezone(timedelta(hours=8))
ROOT = Path(__file__).parent
DEFAULT_DB = ROOT / "routeboard.db"
DEFAULT_OUT_DIR = ROOT / "webapp"


def export_stops_json(db_path=DEFAULT_DB, out_dir=DEFAULT_OUT_DIR) -> tuple[int, str]:
    """导出站点到 stops.json + stops_meta.json。返回 (站点数, 更新时间 ISO)。"""
    db_path = Path(db_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    stops = []
    for r in conn.execute(
        "SELECT operator, source_stop_id, name_tc, name_sc, name_en, "
        "location_tc, location_sc, location_en, latitude, longitude "
        "FROM stop WHERE latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY operator, source_stop_id"
    ):
        stops.append({
            "op": r["operator"], "id": r["source_stop_id"],
            "tc": r["name_tc"] or "", "sc": r["name_sc"] or "", "en": r["name_en"] or "",
            "loc_tc": r["location_tc"] or "", "loc_sc": r["location_sc"] or "", "loc_en": r["location_en"] or "",
            "lat": r["latitude"], "lng": r["longitude"],
        })
    conn.close()

    now = datetime.now(HKT).isoformat()
    _atomic_write_json(out / "stops.json", {"generated": now, "count": len(stops), "stops": stops})
    _atomic_write_json(out / "stops_meta.json", {"count": len(stops), "updated": now})
    return len(stops), now


def _atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


if __name__ == "__main__":
    n, ts = export_stops_json()
    print(f"exported {n} stops -> {DEFAULT_OUT_DIR}/stops.json (updated {ts})")
