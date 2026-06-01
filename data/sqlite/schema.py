"""SQLite 数据库 schema 定义与迁移

当前版本: 1
"""

import sqlite3
from typing import Optional

import logging

from .connection import get_connection

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# ── DDL ──────────────────────────────────────────────────────────────

CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_VIDEOS = """
CREATE TABLE IF NOT EXISTS videos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT    NOT NULL,
    video_path  TEXT    NOT NULL,
    file_size   INTEGER NOT NULL DEFAULT 0,
    modified_time REAL  NOT NULL DEFAULT 0,
    duration    REAL    NOT NULL DEFAULT 0,
    fps         REAL    NOT NULL DEFAULT 0,
    frame_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(hash)
);
"""

CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id          INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    frame             INTEGER NOT NULL,
    timestamp         REAL    NOT NULL DEFAULT 0,
    time_str          TEXT    NOT NULL DEFAULT '',
    temp1_full        REAL,
    temp1_normal      REAL,
    temp1_faulty_digit INTEGER,
    temp2             REAL,
    quality           INTEGER NOT NULL DEFAULT 0,
    UNIQUE(video_id, frame)
);
CREATE INDEX IF NOT EXISTS idx_results_video_frame
    ON results(video_id, frame);
CREATE INDEX IF NOT EXISTS idx_results_video_timestamp
    ON results(video_id, timestamp);
"""

CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    type     TEXT    NOT NULL,
    frame    INTEGER NOT NULL DEFAULT 0,
    time     REAL    NOT NULL DEFAULT 0,
    value    REAL
);
CREATE INDEX IF NOT EXISTS idx_events_video_id
    ON events(video_id);
"""

CREATE_ROI_CONFIGS = """
CREATE TABLE IF NOT EXISTS roi_configs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    rotation_angle REAL   NOT NULL DEFAULT 5.0,
    start_frame   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(video_id)
);
"""

CREATE_ROI_REGIONS = """
CREATE TABLE IF NOT EXISTS roi_regions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    config_id INTEGER NOT NULL REFERENCES roi_configs(id) ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    x         INTEGER NOT NULL,
    y         INTEGER NOT NULL,
    width     INTEGER NOT NULL,
    height    INTEGER NOT NULL,
    UNIQUE(config_id, name)
);
"""

CREATE_BEANS = """
CREATE TABLE IF NOT EXISTS beans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    variety    TEXT    NOT NULL DEFAULT '',
    process    TEXT    NOT NULL DEFAULT '',
    origin     TEXT    NOT NULL DEFAULT '',
    altitude   TEXT    NOT NULL DEFAULT '',
    density    TEXT    NOT NULL DEFAULT '',
    moisture   TEXT    NOT NULL DEFAULT '',
    season     TEXT    NOT NULL DEFAULT '',
    out_of_stock INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name)
);
"""

CREATE_ROAST_SESSIONS = """
CREATE TABLE IF NOT EXISTS roast_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        INTEGER REFERENCES videos(id) ON DELETE SET NULL,
    bean_name       TEXT    NOT NULL DEFAULT '',
    bean_id         INTEGER REFERENCES beans(id) ON DELETE SET NULL,
    heater_initial  REAL    NOT NULL DEFAULT 60.0,
    fan_initial     REAL    NOT NULL DEFAULT 50.0,
    roast_date      TEXT    NOT NULL DEFAULT '',
    roast_time      TEXT    NOT NULL DEFAULT '',
    roast_no        TEXT    NOT NULL DEFAULT '',
    roast_total     TEXT    NOT NULL DEFAULT '',
    green_weight    TEXT    NOT NULL DEFAULT '',
    roasted_weight  TEXT    NOT NULL DEFAULT '',
    weight_loss     TEXT    NOT NULL DEFAULT '',
    density         TEXT    NOT NULL DEFAULT '',
    moisture        TEXT    NOT NULL DEFAULT '',
    notes           TEXT    NOT NULL DEFAULT '',
    frames_path     TEXT,
    source          TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_roast_sessions_video_id
    ON roast_sessions(video_id);
CREATE INDEX IF NOT EXISTS idx_roast_sessions_bean_id
    ON roast_sessions(bean_id);
"""

# 合并所有 DDL，按依赖顺序排列
SCHEMA_DDL = [
    CREATE_SCHEMA_VERSION,
    CREATE_VIDEOS,
    CREATE_BEANS,
    CREATE_RESULTS,
    CREATE_EVENTS,
    CREATE_ROI_CONFIGS,
    CREATE_ROI_REGIONS,
    CREATE_ROAST_SESSIONS,
]


# ── API ──────────────────────────────────────────────────────────────

def ensure_schema(db_path: str) -> None:
    """创建数据库表结构（如果不存在）

    幂等操作，多次调用安全。只新增缺失的表，不修改已有表。
    """
    conn = get_connection(db_path)
    for ddl in SCHEMA_DDL:
        # CREATE INDEX 语句不能放在 execute 的 batch 中
        for statement in ddl.split(';'):
            stmt = statement.strip()
            if stmt:
                conn.execute(stmt)

    # 写入 schema 版本
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
        # schema_version 表不存在（全新数据库），返回 None
        return None


def drop_all_tables(db_path: str) -> None:
    """删除所有表（仅测试用）"""
    conn = get_connection(db_path)
    tables = [
        "roast_sessions", "roi_regions", "roi_configs",
        "events", "results", "beans", "videos", "schema_version",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    logger.warning(f"数据库所有表已删除: {db_path}")
