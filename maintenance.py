"""每日维护：检测新路线 → 增量导入新路线站点 → 重新导出 stops.json。

设计（只增不减）：
- 检测 API 路线列表里 DB 没有的路线（CTB 按 route_no，NLB 按 source_route_id/routeId）
- 增量 upsert 新路线进 route 表
- 抓新路线的站点序列 + 站点详情，upsert 进 stop / route_stop 表
- 重新导出 webapp/stops.json + stops_meta.json
- 已停办路线的站点保留（站点物理存在；详情页由 stop-eta 实时接口决定显示哪些路线）
- 幂等：upsert 重复执行无副作用

由 serve_https.py 后台线程每天 05:00 (HKT) 调用 run_maintenance()。
手动: python3 maintenance.py [--check-only]
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from db import get_conn, upsert_route, upsert_stop, upsert_route_stop
from importers.ctb import fetch_ctb_routes, parse_ctb_routes
from importers.nlb import fetch_nlb_routes, parse_nlb_routes
from importers.ctb_stops import fetch_ctb_route_stops, fetch_ctb_stop_detail, SLEEP as CTB_SLEEP
from importers.nlb_stops import fetch_nlb_route_stops, SLEEP as NLB_SLEEP
from export_stops import export_stops_json, DEFAULT_DB, DEFAULT_OUT_DIR

HKT = timezone(timedelta(hours=8))


def detect_new_routes(conn):
    """对比 API 路线列表与 DB，返回 (ctb_new, nlb_new) 新增路线记录列表。"""
    ctb_records = parse_ctb_routes(fetch_ctb_routes())
    existing_ctb = {r[0] for r in conn.execute("SELECT route_no FROM route WHERE operator='CTB'")}
    ctb_new = [rec for rec in ctb_records if rec["route_no"] not in existing_ctb]

    nlb_records = parse_nlb_routes(fetch_nlb_routes())
    existing_nlb = {r[0] for r in conn.execute("SELECT source_route_id FROM route WHERE operator='NLB'")}
    nlb_new = [rec for rec in nlb_records if rec["source_route_id"] not in existing_nlb]

    return ctb_new, nlb_new


def import_new_ctb_stops(conn, new_records, now) -> tuple[int, int]:
    """抓新增 CTB 路线的站点（序列 + 去重详情），upsert 入库。返回 (新站点数, 关联数)。"""
    stop_ids: set[str] = set()
    links: list[tuple[str, int, int, str]] = []
    for rec in new_records:
        route_no = rec["route_no"]
        for dir_name, dir_int in [("inbound", 0), ("outbound", 1)]:
            try:
                data = fetch_ctb_route_stops(route_no, dir_name)
            except Exception as e:
                print(f"  [warn] CTB {route_no}/{dir_name}: {e}", flush=True)
                continue
            for item in data:
                sid = item.get("stop")
                seq = item.get("seq", 0)
                if sid:
                    stop_ids.add(sid)
                    links.append((route_no, dir_int, seq, sid))
            time.sleep(CTB_SLEEP)

    existing = {r[0] for r in conn.execute("SELECT source_stop_id FROM stop WHERE operator='CTB'")}
    to_fetch = sorted(stop_ids - existing)
    for i, sid in enumerate(to_fetch, 1):
        try:
            d = fetch_ctb_stop_detail(sid)
        except Exception as e:
            print(f"  [warn] CTB stop {sid}: {e}", flush=True)
            continue
        upsert_stop(
            conn, operator="CTB", source_stop_id=sid,
            name_tc=d.get("name_tc"), name_sc=d.get("name_sc"), name_en=d.get("name_en"),
            location_tc=None, location_sc=None, location_en=None,
            latitude=_to_float(d.get("lat")), longitude=_to_float(d.get("long")),
            data_timestamp=d.get("data_timestamp"), fetched_at=now,
        )
        if i % 50 == 0:
            print(f"  CTB stop detail {i}/{len(to_fetch)}", flush=True)
        time.sleep(CTB_SLEEP)

    for route_no, dir_int, seq, sid in links:
        upsert_route_stop(conn, operator="CTB", source_route_id=route_no,
                          direction=dir_int, seq=seq, source_stop_id=sid, fetched_at=now)
    return len(to_fetch), len(links)


def import_new_nlb_stops(conn, new_records, now) -> tuple[int, int]:
    """抓新增 NLB 路线（每个 routeId）的站点，upsert 入库。返回 (新站点数, 关联数)。"""
    seen: set[str] = set()
    link_count = 0
    for rec in new_records:
        route_id = rec["source_route_id"]
        direction = rec["direction"]
        try:
            stops = fetch_nlb_route_stops(route_id)
        except Exception as e:
            print(f"  [warn] NLB routeId={route_id}: {e}", flush=True)
            continue
        for seq, s in enumerate(stops, 1):
            sid = s.get("stopId")
            if not sid:
                continue
            if sid not in seen:
                upsert_stop(
                    conn, operator="NLB", source_stop_id=sid,
                    name_tc=s.get("stopName_c"), name_sc=s.get("stopName_s"), name_en=s.get("stopName_e"),
                    location_tc=s.get("stopLocation_c"), location_sc=s.get("stopLocation_s"), location_en=s.get("stopLocation_e"),
                    latitude=_to_float(s.get("latitude")), longitude=_to_float(s.get("longitude")),
                    data_timestamp=None, fetched_at=now,
                )
                seen.add(sid)
            upsert_route_stop(conn, operator="NLB", source_route_id=route_id,
                              direction=direction, seq=seq, source_stop_id=sid, fetched_at=now)
            link_count += 1
        time.sleep(NLB_SLEEP)
    return len(seen), link_count


def run_maintenance(db_path=DEFAULT_DB, out_dir=DEFAULT_OUT_DIR, check_only: bool = False) -> dict:
    """执行一次维护。返回报告 dict。check_only=True 只检测不导入。"""
    conn = get_conn(db_path)
    now = datetime.now(HKT).isoformat()
    report: dict = {
        "check_time": now,
        "ctb_new_routes": 0, "nlb_new_routes": 0,
        "new_ctb": [], "new_nlb": [],
    }
    try:
        ctb_new, nlb_new = detect_new_routes(conn)
        report["ctb_new_routes"] = len(ctb_new)
        report["nlb_new_routes"] = len(nlb_new)
        report["new_ctb"] = [r["route_no"] for r in ctb_new]
        report["new_nlb"] = [r["route_no"] for r in nlb_new]

        if not (ctb_new or nlb_new):
            print("[maintenance] no new routes", flush=True)
            return report
        if check_only:
            print(f"[maintenance] (check-only) {len(ctb_new)} CTB + {len(nlb_new)} NLB new, skip import", flush=True)
            return report

        # 1) 新路线写入 route 表
        for rec in ctb_new:
            upsert_route(conn, fetched_at=now, **rec)
        for rec in nlb_new:
            upsert_route(conn, fetched_at=now, **rec)
        conn.commit()

        # 2) 抓新路线站点
        if ctb_new:
            s, l = import_new_ctb_stops(conn, ctb_new, now)
            report["ctb_new_stops"] = s
            report["ctb_new_links"] = l
        if nlb_new:
            s, l = import_new_nlb_stops(conn, nlb_new, now)
            report["nlb_new_stops"] = s
            report["nlb_new_links"] = l
        conn.commit()

        # 3) 重新导出 stops.json
        count, ts = export_stops_json(db_path, out_dir)
        report["stops_count"] = count
        report["stops_updated"] = ts
        print(f"[maintenance] +{len(ctb_new)} CTB/{report.get('ctb_new_stops', 0)} stops, "
              f"+{len(nlb_new)} NLB/{report.get('nlb_new_stops', 0)} stops; stops.json -> {count}", flush=True)
        return report
    finally:
        conn.close()


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    import sys
    rep = run_maintenance(check_only="--check-only" in sys.argv)
    print("report:", rep)
