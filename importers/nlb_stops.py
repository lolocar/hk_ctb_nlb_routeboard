"""新世界第一巴士（NLB）站点导入。

一步到位:
https://rt.data.gov.hk/v2/transport/nlb/stop.php?action=list&routeId={routeId}
响应: {"stops":[{stopId, stopName_c/s/e, stopLocation_c/s/e, latitude, longitude,
                fare, fareHoliday, someDepartureObserveOnly}, ...]}

关键点:
- 每个方向使用**独立的 routeId**。同一 routeNo 的多个 routeId（来自 route 表）
  各自对应一个方向/变体，需逐一请求。
  例: B2X 去程 routeId=35（4 站），返程 routeId=36（3 站），站点列表不同。
- 站点直接带 latitude/longitude，无需二次请求。
- route 表中 (route_no, direction) 已按 routeId 升序编号，这里直接按 route 表
  逐行（每行 = 一个 routeId）请求站点。

路线列表从 DB 的 route 表读取（需先运行 import_routes.py）。
"""
from __future__ import annotations

import time
import urllib.request
from datetime import datetime, timezone, timedelta

from db import upsert_stop, upsert_route_stop

NLB_STOP_URL = "https://rt.data.gov.hk/v2/transport/nlb/stop.php"
HKT = timezone(timedelta(hours=8))
UA = {"User-Agent": "routeboard-import/0.1"}
SLEEP = 0.15


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        import json
        return json.loads(resp.read().decode("utf-8"))


def get_nlb_route_rows(conn) -> list[dict]:
    """从 DB 读取新巴路线行（每行 = 一个 routeId + direction）。"""
    rows = conn.execute(
        "SELECT route_no, source_route_id, direction FROM route "
        "WHERE operator='NLB' ORDER BY route_no, direction"
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_nlb_route_stops(route_id: str) -> list[dict]:
    """抓取某 routeId（一个方向）的站点列表（含位置）。"""
    url = f"{NLB_STOP_URL}?action=list&routeId={urllib.parse.quote(route_id)}"
    payload = _get_json(url)
    return payload.get("stops", [])


def import_nlb_stops(conn) -> tuple[int, int]:
    """抓取并写入新巴站点 + 路线-站点关联。返回 (站点数, 关联数)。"""
    now = datetime.now(HKT).isoformat()
    route_rows = get_nlb_route_rows(conn)
    print(f"NLB: {len(route_rows)} route directions to process")

    seen_stops: set[str] = set()
    link_count = 0

    for row in route_rows:
        route_id = row["source_route_id"]
        route_no = row["route_no"]
        direction = row["direction"]
        try:
            stops = fetch_nlb_route_stops(route_id)
        except Exception as e:
            print(f"  [warn] stops routeId={route_id} ({route_no} d{direction}) failed: {e}")
            continue

        for seq, s in enumerate(stops, start=1):
            stop_id = s.get("stopId")
            if not stop_id:
                continue
            # 站点详情只写一次（跨路线共享，后写覆盖无妨，内容相同）
            if stop_id not in seen_stops:
                upsert_stop(
                    conn,
                    operator="NLB",
                    source_stop_id=stop_id,
                    name_tc=s.get("stopName_c"),
                    name_sc=s.get("stopName_s"),
                    name_en=s.get("stopName_e"),
                    location_tc=s.get("stopLocation_c"),
                    location_sc=s.get("stopLocation_s"),
                    location_en=s.get("stopLocation_e"),
                    latitude=_to_float(s.get("latitude")),
                    longitude=_to_float(s.get("longitude")),
                    data_timestamp=None,
                    fetched_at=now,
                )
                seen_stops.add(stop_id)
            upsert_route_stop(
                conn,
                operator="NLB",
                source_route_id=route_id,
                direction=direction,
                seq=seq,
                source_stop_id=stop_id,
                fetched_at=now,
            )
            link_count += 1
        time.sleep(SLEEP)

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('nlb_stops_last_fetch', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (now,),
    )
    return len(seen_stops), link_count


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
    replace_operator_stops(conn, "NLB")
    n_stops, n_links = import_nlb_stops(conn)
    conn.commit()
    print(f"NLB: imported {n_stops} stops, {n_links} route-stop links")
