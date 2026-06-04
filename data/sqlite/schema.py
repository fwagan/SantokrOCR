"""SQLite 数据库 schema 定义与迁移

当前版本: 2
"""

import sqlite3
from typing import Optional

import logging

from .connection import get_connection

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# ── DDL ──────────────────────────────────────────────────────────────

CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_BEAN = """
CREATE TABLE IF NOT EXISTS bean (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    variety      TEXT    NOT NULL DEFAULT '',
    process      TEXT    NOT NULL DEFAULT '',
    origin       TEXT    NOT NULL DEFAULT '',
    altitude     TEXT    NOT NULL DEFAULT '',
    density      REAL,
    moisture     REAL,
    season       TEXT    NOT NULL DEFAULT '',
    out_of_stock INTEGER NOT NULL DEFAULT 0,
    is_deleted   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name)
);
"""

CREATE_ROAST_SESSION = """
CREATE TABLE IF NOT EXISTS roast_session (
    session_id          TEXT    PRIMARY KEY,
    is_raw_data         INTEGER NOT NULL DEFAULT 0,
    bean_id             INTEGER REFERENCES bean(id) ON DELETE SET NULL,
    heater_initial      REAL    NOT NULL DEFAULT 60.0,
    fan_initial         REAL    NOT NULL DEFAULT 50.0,
    density_override    REAL,
    moisture_override   REAL,
    roast_date          TEXT    NOT NULL DEFAULT '',
    roast_time          TEXT    NOT NULL DEFAULT '',
    roast_no            TEXT    NOT NULL DEFAULT '',
    roast_total         TEXT    NOT NULL DEFAULT '',
    green_weight        REAL,
    roasted_weight      REAL,
    notes               TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_roast_session_bean_id
    ON roast_session(bean_id);
CREATE INDEX IF NOT EXISTS idx_roast_session_created_at
    ON roast_session(created_at);
"""

CREATE_RESULT = """
CREATE TABLE IF NOT EXISTS result (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT    NOT NULL REFERENCES roast_session(session_id) ON DELETE CASCADE,
    frame              INTEGER NOT NULL,
    timestamp          REAL    NOT NULL DEFAULT 0,
    original_timestamp REAL,
    time_str           TEXT    NOT NULL DEFAULT '',
    timer              TEXT,
    temp1_full         TEXT    NOT NULL DEFAULT '',
    temp1_normal       TEXT    NOT NULL DEFAULT '',
    temp1_faulty_digit INTEGER DEFAULT -1,
    temp2              TEXT    NOT NULL DEFAULT '',
    abnormal_category  TEXT,
    UNIQUE(session_id, frame)
);
CREATE INDEX IF NOT EXISTS idx_result_session_frame
    ON result(session_id, frame);
CREATE INDEX IF NOT EXISTS idx_result_session_timestamp
    ON result(session_id, timestamp);
"""

CREATE_EVENT = """
CREATE TABLE IF NOT EXISTS event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL REFERENCES roast_session(session_id) ON DELETE CASCADE,
    type       TEXT    NOT NULL,
    frame      INTEGER NOT NULL DEFAULT 0,
    time       REAL    NOT NULL DEFAULT 0,
    value      REAL
);
CREATE INDEX IF NOT EXISTS idx_event_session_id
    ON event(session_id);
"""

# 合并所有 DDL，按依赖顺序排列
SCHEMA_DDL = [
    CREATE_SCHEMA_VERSION,
    CREATE_BEAN,
    CREATE_ROAST_SESSION,
    CREATE_RESULT,
    CREATE_EVENT,
]


# ── API ──────────────────────────────────────────────────────────────

def ensure_schema(db_path: str) -> None:
    """创建数据库表结构（如果不存在）

    幂等操作，多次调用安全。只新增缺失的表，不修改已有表。
    """
    conn = get_connection(db_path)
    for ddl in SCHEMA_DDL:
        for statement in ddl.split(';'):
            stmt = statement.strip()
            if stmt:
                conn.execute(stmt)

    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    logger.info(f"数据库 schema 已就绪 (v{SCHEMA_VERSION}): {db_path}")


def get_schema_version(db_path: str) -> Optional[int]:
    """查询当前数据库 schema 版本号"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(version) FROM schema_version"
        ).fetchone()
        return row[0] if row and row[0] is not None else None
    except sqlite3.OperationalError:
        return None


def drop_all_tables(db_path: str) -> None:
    """删除所有表（仅测试用）"""
    conn = get_connection(db_path)
    tables = [
        "result", "event", "roast_session", "bean", "schema_version",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    logger.warning(f"数据库所有表已删除: {db_path}")
