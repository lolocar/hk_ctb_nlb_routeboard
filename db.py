"""SQLite 数据库：schema 定义与连接管理。

统一路线模型（route）：
- operator: 运营公司代码（CTB / NLB）
- route_id: 源系统内部 ID（NLB 有数字 routeId；CTB 无，用 route_no 作主键一部分）
- route_no: 路线编号（如 "1"、"101X"、"A35"）
- direction: 方向序号（同一 route_no 可能有多条记录，如往返、不同支线）
- origin_tc/origin_sc/origin_en, destination_tc/destination_sc/destination_en
- overnight / special: 标志位（NLB 提供；CTB 从 route_no 前缀推断 N 开头为通宵）
- data_timestamp: 源数据时间戳
- fetched_at: 本次抓取时间（本地）

CTB 一条记录天然就是一个方向（orig→dest），direction 按抓取顺序编号。
NLB 同一 routeNo 可能有多条 routeId（往返/支线），direction 按 routeId 顺序编号。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "routeboard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS route (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator TEXT NOT NULL,              -- 'CTB' | 'NLB'
    source_route_id TEXT,                -- 源系统 ID（NLB: routeId 字符串; CTB: NULL）
    route_no TEXT NOT NULL,              -- 路线编号
    direction INTEGER NOT NULL DEFAULT 0,
    origin_tc TEXT,
    origin_sc TEXT,
    origin_en TEXT,
    destination_tc TEXT,
    destination_sc TEXT,
    destination_en TEXT,
    overnight INTEGER NOT NULL DEFAULT 0,
    special INTEGER NOT NULL DEFAULT 0,
    data_timestamp TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE (operator, source_route_id, direction)
);

CREATE INDEX IF NOT EXISTS idx_route_operator_no ON route (operator, route_no);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- 物理站点（跨路线共享）。同一 (operator, source_stop_id) 唯一。
-- CTB stop id 形如 "002511"；NLB stop id 形如 "176"。
CREATE TABLE IF NOT EXISTS stop (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator TEXT NOT NULL,              -- 'CTB' | 'NLB'
    source_stop_id TEXT NOT NULL,        -- 源系统站点 ID
    name_tc TEXT,
    name_sc TEXT,
    name_en TEXT,
    location_tc TEXT,                    -- 所在路段（NLB 提供；CTB 无）
    location_sc TEXT,
    location_en TEXT,
    latitude REAL,
    longitude REAL,
    data_timestamp TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE (operator, source_stop_id)
);

CREATE INDEX IF NOT EXISTS idx_stop_operator ON stop (operator);
CREATE INDEX IF NOT EXISTS idx_stop_coords ON stop (latitude, longitude);

-- 路线（含方向）与站点的关联，带行驶顺序 seq。
-- 关联 route 表: (operator, source_route_id, direction)。
CREATE TABLE IF NOT EXISTS route_stop (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator TEXT NOT NULL,
    source_route_id TEXT NOT NULL,       -- 源路线 ID（CTB: route 号; NLB: routeId）
    direction INTEGER NOT NULL DEFAULT 0,
    seq INTEGER NOT NULL,                -- 该方向上的站点顺序（从 1 开始）
    source_stop_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    UNIQUE (operator, source_route_id, direction, seq)
);

CREATE INDEX IF NOT EXISTS idx_route_stop_route ON route_stop (operator, source_route_id, direction);
CREATE INDEX IF NOT EXISTS idx_route_stop_stop ON route_stop (source_stop_id);

-- 站点坐标人工覆写（数据源坐标不准时手动指定，导入/维护后自动套用）。
-- 例: NLB 152 深圳灣口岸 → 用 CTB 003208 的坐标。
CREATE TABLE IF NOT EXISTS stop_override (
    operator TEXT NOT NULL,
    source_stop_id TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    note TEXT,
    PRIMARY KEY (operator, source_stop_id)
);
"""


def get_conn(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_route(
    conn: sqlite3.Connection,
    *,
    operator: str,
    source_route_id: str | None,
    route_no: str,
    direction: int,
    origin_tc: str | None,
    origin_sc: str | None,
    origin_en: str | None,
    destination_tc: str | None,
    destination_sc: str | None,
    destination_en: str | None,
    overnight: int,
    special: int,
    data_timestamp: str | None,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO route (
            operator, source_route_id, route_no, direction,
            origin_tc, origin_sc, origin_en,
            destination_tc, destination_sc, destination_en,
            overnight, special, data_timestamp, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (operator, source_route_id, direction) DO UPDATE SET
            route_no = excluded.route_no,
            origin_tc = excluded.origin_tc,
            origin_sc = excluded.origin_sc,
            origin_en = excluded.origin_en,
            destination_tc = excluded.destination_tc,
            destination_sc = excluded.destination_sc,
            destination_en = excluded.destination_en,
            overnight = excluded.overnight,
            special = excluded.special,
            data_timestamp = excluded.data_timestamp,
            fetched_at = excluded.fetched_at
        """,
        (
            operator, source_route_id, route_no, direction,
            origin_tc, origin_sc, origin_en,
            destination_tc, destination_sc, destination_en,
            overnight, special, data_timestamp, fetched_at,
        ),
    )


def replace_operator_routes(conn: sqlite3.Connection, operator: str) -> None:
    """导入前清掉该公司的旧数据（全量替换策略）。"""
    conn.execute("DELETE FROM route WHERE operator = ?", (operator,))


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def upsert_stop(
    conn: sqlite3.Connection,
    *,
    operator: str,
    source_stop_id: str,
    name_tc: str | None,
    name_sc: str | None,
    name_en: str | None,
    location_tc: str | None,
    location_sc: str | None,
    location_en: str | None,
    latitude: float | None,
    longitude: float | None,
    data_timestamp: str | None,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO stop (
            operator, source_stop_id,
            name_tc, name_sc, name_en,
            location_tc, location_sc, location_en,
            latitude, longitude, data_timestamp, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (operator, source_stop_id) DO UPDATE SET
            name_tc = excluded.name_tc,
            name_sc = excluded.name_sc,
            name_en = excluded.name_en,
            location_tc = excluded.location_tc,
            location_sc = excluded.location_sc,
            location_en = excluded.location_en,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            data_timestamp = excluded.data_timestamp,
            fetched_at = excluded.fetched_at
        """,
        (
            operator, source_stop_id,
            name_tc, name_sc, name_en,
            location_tc, location_sc, location_en,
            latitude, longitude, data_timestamp, fetched_at,
        ),
    )


def upsert_route_stop(
    conn: sqlite3.Connection,
    *,
    operator: str,
    source_route_id: str,
    direction: int,
    seq: int,
    source_stop_id: str,
    fetched_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO route_stop (
            operator, source_route_id, direction, seq, source_stop_id, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (operator, source_route_id, direction, seq) DO UPDATE SET
            source_stop_id = excluded.source_stop_id,
            fetched_at = excluded.fetched_at
        """,
        (operator, source_route_id, direction, seq, source_stop_id, fetched_at),
    )


def replace_operator_stops(conn: sqlite3.Connection, operator: str) -> None:
    """导入前清掉该公司的旧站点与路线-站点关联（全量替换策略）。"""
    conn.execute("DELETE FROM route_stop WHERE operator = ?", (operator,))
    conn.execute("DELETE FROM stop WHERE operator = ?", (operator,))


def apply_stop_overrides(conn: sqlite3.Connection) -> int:
    """将 stop_override 表中的人工坐标覆写套用到 stop 表。返回覆写的站点数。

    在每次站点导入/维护后调用，确保人工指定的坐标不会被数据源覆盖。
    """
    cur = conn.execute(
        """
        UPDATE stop SET latitude = o.latitude, longitude = o.longitude
        FROM stop_override o
        WHERE stop.operator = o.operator
          AND stop.source_stop_id = o.source_stop_id
        """
    )
    return cur.rowcount or 0
