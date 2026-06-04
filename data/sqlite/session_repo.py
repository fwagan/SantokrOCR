"""SqliteSessionRepository：基于 SQLite 的烘焙会话存储"""

from typing import List, Optional

import logging

from data.sqlite.connection import execute_with_lock
from data.sqlite.schema import ensure_schema
from data.types import RoastSession

logger = logging.getLogger(__name__)

# roast_session 表列名
_SESSION_COLS = [
    'session_id', 'is_raw_data', 'bean_id',
    'heater_initial', 'fan_initial',
    'density_override', 'moisture_override',
    'roast_date', 'roast_time', 'roast_no', 'roast_total',
    'green_weight', 'roasted_weight',
    'notes', 'created_at', 'updated_at',
]
_COL_NAMES = [c for c in _SESSION_COLS if c not in ('created_at', 'updated_at')]
_COL_LIST = ', '.join(_COL_NAMES)
_DISPLAY_COLS = ', '.join(c for c in _SESSION_COLS if c != 'created_at')
_PLACEHOLDERS = ', '.join('?' for _ in _COL_NAMES)
_UPDATE_SET = ', '.join(f"{c} = excluded.{c}" for c in _COL_NAMES if c != 'session_id')
_UPSERT_SQL = (
    f"INSERT INTO roast_session ({_COL_LIST}) VALUES ({_PLACEHOLDERS}) "
    f"ON CONFLICT(session_id) DO UPDATE SET {_UPDATE_SET}, "
    f"updated_at = datetime('now')"
)


def _row_to_session(row) -> RoastSession:
    """将 sqlite3.Row 转为 RoastSession"""
    return {
        'session_id': row['session_id'],
        'is_raw_data': bool(row['is_raw_data']),
        'heater_initial': row['heater_initial'],
        'fan_initial': row['fan_initial'],
        'bean_id': row['bean_id'] if row['bean_id'] is not None else None,
        'density_override': row['density_override'],
        'moisture_override': row['moisture_override'],
        'roast_date': row['roast_date'] or '',
        'roast_time': row['roast_time'] or '',
        'roast_no': row['roast_no'] or '',
        'roast_total': row['roast_total'] or '',
        'green_weight': row['green_weight'],
        'roasted_weight': row['roasted_weight'],
        'notes': row['notes'] or '',
    }


class SqliteSessionRepository:
    """基于 SQLite 的烘焙会话仓库

    存储会话元信息（heater_initial, fan_initial, roast_info 等）。
    results 和 events 由各自的 Repository 管理。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        ensure_schema(db_path)

    def save(self, session_id: str, session: RoastSession) -> None:
        def _save(conn):
            values = {
                'session_id': session_id,
                'is_raw_data': 1 if session.get('is_raw_data') else 0,
                'bean_id': session.get('bean_id'),
                'heater_initial': session.get('heater_initial', 60.0),
                'fan_initial': session.get('fan_initial', 50.0),
                'density_override': session.get('density_override'),
                'moisture_override': session.get('moisture_override'),
                'roast_date': session.get('roast_date', ''),
                'roast_time': session.get('roast_time', ''),
                'roast_no': session.get('roast_no', ''),
                'roast_total': session.get('roast_total', ''),
                'green_weight': session.get('green_weight'),
                'roasted_weight': session.get('roasted_weight'),
                'notes': session.get('notes', ''),
            }
            conn.execute(
                _UPSERT_SQL,
                tuple(values[c] for c in _COL_NAMES),
            )
            conn.commit()
        execute_with_lock(self.db_path, _save)

    def load(self, session_id: str) -> Optional[RoastSession]:
        def _load(conn):
            row = conn.execute(
                f"SELECT {_DISPLAY_COLS} FROM roast_session WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return _row_to_session(row) if row else None
        return execute_with_lock(self.db_path, _load)

    def delete(self, session_id: str) -> None:
        def _delete(conn):
            conn.execute(
                "DELETE FROM roast_session WHERE session_id = ?", (session_id,),
            )
            conn.commit()
        execute_with_lock(self.db_path, _delete)

    def list_all(self) -> List[RoastSession]:
        def _list(conn):
            rows = conn.execute(
                f"SELECT {_DISPLAY_COLS} FROM roast_session ORDER BY created_at DESC"
            ).fetchall()
            return [_row_to_session(r) for r in rows]
        return execute_with_lock(self.db_path, _list)
