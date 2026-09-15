"""城巴（CTB）路线列表导入。

源 API: https://rt.data.gov.hk/v1/transport/citybus-nwfb/route/ctb
响应结构:
{
  "type": "RouteList",
  "version": "1.0",
  "generated_timestamp": "...",
  "data": [
    {
      "co": "CTB",
      "route": "1",
      "orig_tc": "中環 (港澳碼頭)", "orig_en": "Central (Macao Ferry)",
      "dest_tc": "跑馬地 (上)", "dest_en": "Happy Valley (Upper)",
      "orig_sc": "中环 (港澳码头)", "dest_sc": "跑马地 (上)",
      "data_timestamp": "..."
    }, ...
  ]
}

注意: 该 endpoint 挂在 citybus-nwfb 路径下，返回的是城巴（CTB）数据。
每条记录 = 一个方向的路线。source_route_id 用 route 号本身（CTB 无独立 ID）。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone, timedelta

from db import upsert_route

CTB_ROUTE_URL = "https://rt.data.gov.hk/v1/transport/citybus-nwfb/route/ctb"
HKT = timezone(timedelta(hours=8))


def fetch_ctb_routes(url: str = CTB_ROUTE_URL) -> dict:
    """抓取城巴路线列表原始 JSON。"""
    req = urllib.request.Request(url, headers={"User-Agent": "routeboard-import/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_ctb_routes(payload: dict) -> list[dict]:
    """把 API 响应解析成统一路线记录列表。"""
    records = []
    for item in payload.get("data", []):
        route_no = item.get("route", "")
        # CTB 无独立 routeId，用 route_no 作为 source_route_id
        records.append(
            {
                "operator": "CTB",
                "source_route_id": route_no,
                "route_no": route_no,
                "direction": 0,  # CTB 每条记录即一个方向，无同 route_no 多记录
                "origin_tc": item.get("orig_tc"),
                "origin_sc": item.get("orig_sc"),
                "origin_en": item.get("orig_en"),
                "destination_tc": item.get("dest_tc"),
                "destination_sc": item.get("dest_sc"),
                "destination_en": item.get("dest_en"),
                # N 前缀视为通宵线
                "overnight": 1 if route_no.upper().startswith("N") else 0,
                "special": 0,
                "data_timestamp": item.get("data_timestamp"),
            }
        )
    return records


def import_ctb_routes(conn, url: str = CTB_ROUTE_URL) -> int:
    """抓取 + 解析 + 写入城巴路线，返回写入条数。"""
    payload = fetch_ctb_routes(url)
    records = parse_ctb_routes(payload)
    now = datetime.now(HKT).isoformat()
    for rec in records:
        upsert_route(conn, fetched_at=now, **rec)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('ctb_last_fetch', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (now,),
    )
    return len(records)


if __name__ == "__main__":
    from db import get_conn, replace_operator_routes

    conn = get_conn()
    replace_operator_routes(conn, "CTB")
    n = import_ctb_routes(conn)
    conn.commit()
    print(f"CTB: imported {n} routes")
