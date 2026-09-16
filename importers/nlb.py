"""新世界第一巴士（NLB）路线列表导入。

源 API: https://rt.data.gov.hk/v2/transport/nlb/route.php?action=list
响应结构:
{
  "routes": [
    {
      "routeId": "1",
      "routeNo": "1",
      "routeName_c": "梅窩碼頭 > 大澳",
      "routeName_s": "梅窝码头 > 大澳",
      "routeName_e": "Mui Wo Ferry Pier > Tai O",
      "overnightRoute": 0,
      "specialRoute": 0
    }, ...
  ]
}

注意:
- 同一 routeNo 可能有多条 routeId（往返、不同支线/变体），
  direction 按 routeId 升序编号（0,1,2,...）。
- routeName 用 " > " 拼接起讫点，需拆分。
  分隔符实际为 " > "（前后各一个空格），但部分数据可能有多余空格，做宽松匹配。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta

from db import upsert_route

NLB_ROUTE_URL = "https://rt.data.gov.hk/v2/transport/nlb/route.php?action=list"
HKT = timezone(timedelta(hours=8))

# 宽松匹配 " > " 分隔符（允许两侧任意空白）
_SPLIT_RE = re.compile(r"\s*>\s*")


def fetch_nlb_routes(url: str = NLB_ROUTE_URL) -> dict:
    """抓取新巴路线列表原始 JSON。"""
    req = urllib.request.Request(url, headers={"User-Agent": "routeboard-import/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _split_route_name(name: str) -> tuple[str | None, str | None]:
    """把 '起点 > 终点' 拆成 (起点, 终点)。无法拆分时返回 (name, None)。"""
    parts = _SPLIT_RE.split(name.strip())
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return name.strip(), None


def parse_nlb_routes(payload: dict) -> list[dict]:
    """把 API 响应解析成统一路线记录列表（direction 按 routeId 升序分配）。"""
    raw = payload.get("routes", [])
    # 按 routeNo 分组，组内按 routeId 排序
    groups: dict[str, list[dict]] = {}
    for item in raw:
        groups.setdefault(item.get("routeNo", ""), []).append(item)

    records = []
    for route_no in sorted(groups, key=lambda r: (len(r), r)):
        items = sorted(groups[route_no], key=lambda x: int(x.get("routeId", 0) or 0))
        for direction, item in enumerate(items):
            orig_tc, dest_tc = _split_route_name(item.get("routeName_c", ""))
            orig_sc, dest_sc = _split_route_name(item.get("routeName_s", ""))
            orig_en, dest_en = _split_route_name(item.get("routeName_e", ""))
            records.append(
                {
                    "operator": "NLB",
                    "source_route_id": item.get("routeId"),
                    "route_no": route_no,
                    "direction": direction,
                    "origin_tc": orig_tc,
                    "origin_sc": orig_sc,
                    "origin_en": orig_en,
                    "destination_tc": dest_tc,
                    "destination_sc": dest_sc,
                    "destination_en": dest_en,
                    "overnight": int(item.get("overnightRoute", 0)),
                    "special": int(item.get("specialRoute", 0)),
                    "data_timestamp": None,  # NLB 列表接口不提供时间戳
                }
            )
    return records


def import_nlb_routes(conn, url: str = NLB_ROUTE_URL) -> int:
    """抓取 + 解析 + 写入新巴路线，返回写入条数。"""
    payload = fetch_nlb_routes(url)
    records = parse_nlb_routes(payload)
    now = datetime.now(HKT).isoformat()
    for rec in records:
        upsert_route(conn, fetched_at=now, **rec)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('nlb_last_fetch', ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (now,),
    )
    return len(records)


if __name__ == "__main__":
    from db import get_conn, replace_operator_routes

    conn = get_conn()
    replace_operator_routes(conn, "NLB")
    n = import_nlb_routes(conn)
    conn.commit()
    print(f"NLB: imported {n} routes")
