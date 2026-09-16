"""城巴（CTB）站点导入。

两步走:
1. 路线站点序列（无位置）:
   https://rt.data.gov.hk/v1/transport/citybus-nwfb/route-stop/ctb/{route}/{inbound|outbound}
   响应: {"type":"RouteStop","data":[{co,route,dir("I"/"O"),seq,stop,data_timestamp},...]}
   - 每条路线需分别请求 inbound 和 outbound 两个方向
   - 方向映射: inbound -> direction 0, outbound -> direction 1（与 route 表 direction 对齐）

2. 站点详情（名称+坐标）——**无批量接口，只能逐个查**:
   https://rt.data.gov.hk/v1/transport/citybus-nwfb/stop/{stop_id}
   响应: {"type":"Stop","data":{stop,name_tc,name_sc,name_en,lat,long,data_timestamp}}
   - 注意字段名是 lat / long（不是 lng）

流程:
  Phase 1: 遍历所有路线 x 2 方向，提取 stop ID（stop 字段）并建立
           (route, dir, seq, stop_id) 关联，stop ID 去重。
  Phase 2: 对去重后的 stop ID 逐个请求详情（名称+坐标）。
  Phase 3: 统一写库（stop 表 + route_stop 表）。

路线列表从 DB 的 route 表读取（需先运行 import_routes.py）。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from db import upsert_stop, upsert_route_stop

BASE = "https://rt.data.gov.hk/v1/transport/citybus-nwfb"
HKT = timezone(timedelta(hours=8))
UA = {"User-Agent": "routeboard-import/0.1"}
SLEEP = 0.15  # 请求间隔，避免过快


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_ctb_route_list(conn) -> list[str]:
    """从 DB 读取城巴路线号列表（去重，保持顺序）。"""
    rows = conn.execute(
        "SELECT DISTINCT route_no FROM route WHERE operator='CTB' ORDER BY route_no"
    ).fetchall()
    return [r["route_no"] for r in rows]


def fetch_ctb_route_stops(route_no: str, direction: str) -> list[dict]:
    """抓取某路线某方向的站点序列（无位置）。direction: 'inbound' | 'outbound'。"""
    url = f"{BASE}/route-stop/ctb/{urllib.parse.quote(route_no)}/{direction}"
    payload = _get_json(url)
    return payload.get("data", [])


def fetch_ctb_stop_detail(stop_id: str) -> dict:
    """抓取单个站点详情（名称+坐标）。CTB 无批量接口，只能逐个请求。"""
    url = f"{BASE}/stop/{urllib.parse.quote(stop_id)}"
    payload = _get_json(url)
    return payload.get("data", {})


def collect_ctb_stop_ids(conn, routes: list[str] | None = None) -> list[tuple[str, int, int, str]]:
    """Phase 1: 遍历所有路线 x 2 方向，提取 stop ID 并建立关联。

    返回: [(route_no, direction_int, seq, stop_id), ...]（stop_id 未去重，关联需要）
    """
    if routes is None:
        routes = get_ctb_route_list(conn)

    dir_map = {"inbound": 0, "outbound": 1}
    route_stop_rows: list[tuple[str, int, int, str]] = []
    total = len(routes) * 2
    done = 0

    for route_no in routes:
        for dir_name, dir_int in dir_map.items():
            try:
                data = fetch_ctb_route_stops(route_no, dir_name)
            except Exception as e:
                print(f"  [warn] route-stop {route_no}/{dir_name} failed: {e}")
                done += 1
                continue
            for item in data:
                stop_id = item.get("stop")
                seq = item.get("seq", 0)
                if not stop_id:
                    continue
                route_stop_rows.append((route_no, dir_int, seq, stop_id))
            done += 1
            if done % 50 == 0:
                print(f"  route-stop: {done}/{total}")
            time.sleep(SLEEP)

    return route_stop_rows


def fetch_ctb_stop_details(stop_ids: list[str]) -> dict[str, dict]:
    """Phase 2: 对去重后的 stop ID 逐个请求详情（名称+坐标）。

    返回: {stop_id: detail_dict}
    """
    stop_details: dict[str, dict] = {}
    for i, stop_id in enumerate(stop_ids, 1):
        try:
            stop_details[stop_id] = fetch_ctb_stop_detail(stop_id)
        except Exception as e:
            print(f"  [warn] stop detail {stop_id} failed: {e}")
            stop_details[stop_id] = {"stop": stop_id}
        if i % 100 == 0:
            print(f"  stop detail: {i}/{len(stop_ids)}")
        time.sleep(SLEEP)
    return stop_details


def write_ctb_stops(conn, stop_details: dict[str, dict],
                    route_stop_rows: list[tuple[str, int, int, str]]) -> None:
    """Phase 3: 统一写库（stop 表 + route_stop 表）。"""
    now = datetime.now(HKT).isoformat()

    for stop_id, d in stop_details.items():
        upsert_stop(
            conn,
            operator="CTB",
            source_stop_id=stop_id,
            name_tc=d.get("name_tc"),
            name_sc=d.get("name_sc"),
            name_en=d.get("name_en"),
            location_tc=None,
            location_sc=None,
            location_en=None,
            latitude=_to_float(d.get("lat")),
            longitude=_to_float(d.get("long")),
            data_timestamp=d.get("data_timestamp"),
            fetched_at=now,
        )

    for route_no, dir_int, seq, stop_id in route_stop_rows:
        upsert_route_stop(
            conn,
            operator="CTB",
            source_route_id=route_no,
            direction=dir_int,
            seq=seq,
            source_stop_id=stop_id,
            fetched_at=now,
        )

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('ctb_stops_last_fetch', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (now,),
    )


def import_ctb_stops(conn) -> tuple[int, int]:
    """抓取并写入城巴站点 + 路线-站点关联。返回 (站点数, 关联数)。

    流程:
      Phase 1: 遍历路线 x 2 方向，提取 stop ID（去重）
      Phase 2: 逐个请求 stop 详情（名称+坐标）
      Phase 3: 统一写库
    """
    routes = get_ctb_route_list(conn)
    print(f"CTB: {len(routes)} routes to process")

    # Phase 1: 提取 stop ID + 建立关联
    print("Phase 1: collecting stop IDs from route-stop sequences...")
    route_stop_rows = collect_ctb_stop_ids(conn, routes)
    stop_ids = sorted({row[3] for row in route_stop_rows})
    print(f"  -> {len(route_stop_rows)} links, {len(stop_ids)} unique stops")

    # Phase 2: 逐个查询站点位置
    print("Phase 2: fetching stop details (one request per stop, no batch API)...")
    stop_details = fetch_ctb_stop_details(stop_ids)

    # Phase 3: 统一写库
    print("Phase 3: writing to database...")
    write_ctb_stops(conn, stop_details, route_stop_rows)

    return len(stop_details), len(route_stop_rows)


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    from db import get_conn, replace_operator_stops

    conn = get_conn()
    replace_operator_stops(conn, "CTB")
    n_stops, n_links = import_ctb_stops(conn)
    conn.commit()
    print(f"CTB: imported {n_stops} stops, {n_links} route-stop links")
