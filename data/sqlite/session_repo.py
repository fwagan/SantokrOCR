"""SqliteSessionRepository：基于 SQLite 的烘焙会话存储"""

from typing import List, Optional

import logging

from data.sqlite.connection import execute_with_lock, get_default_db_path
from data.sqlite.schema import ensure_schema
from data.types import RoastSession

logger = logging.getLogger(__name__)

# roast_session 表列名
_SESSION_COLS = [
    'session_id', 'is_raw_data', 'is_favorite', 'bean_id',
    'heater_initial', 'fan_initial',
    'density_override', 'moisture_override',
    'roast_date', 'roast_time', 'roast_no', 'roast_total',
    'green_weight', 'roasted_weight',
    'notes', 'created_at', 'updated_at',
]
_COL_NAMES = [c for c in _SESSION_COLS if c not in ('created_at', 'updated_at')]
_COL_LIST = ', '.join(_COL_NAMES)
_DISPLAY_COLS = ', '.join(_SESSION_COLS)
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
        'is_favorite': bool(row['is_favorite']),
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
        'created_at': row['created_at'] or '',
        'updated_at': row['updated_at'] or '',
    }


class SqliteSessionRepository:
    """基于 SQLite 的烘焙会话仓库

    存储会话元信息（heater_initial, fan_initial, roast_info 等）。
    results 和 events 由各自的 Repository 管理。
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_default_db_path()
        ensure_schema(self.db_path)

    def _build_values(self, session_id: str, session: RoastSession) -> dict:
        return {
            'session_id': session_id,
            'is_raw_data': 1 if session.get('is_raw_data') else 0,
            'is_favorite': 1 if session.get('is_favorite') else 0,
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

    def update_fields(self, session_id: str, **fields) -> None:
        """更新会话的指定字段"""
        def _update(conn):
            set_clause = ', '.join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [session_id]
            conn.execute(
                f"UPDATE roast_session SET {set_clause} WHERE session_id = ?",
                values,
            )
            conn.commit()
        execute_with_lock(self.db_path, _update)

    def save(self, session_id: str, session: RoastSession) -> None:
        def _save(conn):
            self._upsert(conn, session_id, session)
            conn.commit()
        execute_with_lock(self.db_path, _save)

    def save_with_conn(self, conn, session_id: str, session: RoastSession) -> None:
        """使用外部连接写入但不提交（用于事务协调）"""
        self._upsert(conn, session_id, session)

    def _upsert(self, conn, session_id: str, session: RoastSession) -> None:
        values = self._build_values(session_id, session)
        conn.execute(
            _UPSERT_SQL,
            tuple(values[c] for c in _COL_NAMES),
        )

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

    def list_filtered(self, date_from: str = '', date_to: str = '',
                       bean_id: Optional[int] = None,
                       is_raw_data: bool = False) -> List[dict]:
        """按日期和豆名筛选会话，JOIN bean 表返回富化结果

        Returns:
            包含 bean 信息的 dict 列表（含 bean_name, bean_variety, bean_origin）
        """
        _filter_cols = ', '.join(f'rs.{c}' for c in _SESSION_COLS)

        def _list(conn):
            query = (
                f"SELECT {_filter_cols}, "
                "b.name AS bean_name, b.variety AS bean_variety, "
                "b.origin AS bean_origin "
                "FROM roast_session rs "
                "LEFT JOIN bean b ON rs.bean_id = b.id "
                "WHERE rs.is_raw_data = ?"
            )
            params: list = [1 if is_raw_data else 0]

            if date_from:
                query += " AND rs.roast_date >= ?"
                params.append(date_from)
            if date_to:
                query += " AND rs.roast_date <= ?"
                params.append(date_to)
            if bean_id is not None:
                query += " AND rs.bean_id = ?"
                params.append(bean_id)

            query += " ORDER BY rs.roast_date DESC, rs.roast_time DESC, rs.created_at DESC"
            rows = conn.execute(query, params).fetchall()
            result = []
            for r in rows:
                session = _row_to_session(r)
                session['bean_name'] = r['bean_name'] or ''
                session['bean_variety'] = r['bean_variety'] or ''
                session['bean_origin'] = r['bean_origin'] or ''
                result.append(session)
            return result
        return execute_with_lock(self.db_path, _list)

    def get_display_name(self, session_id: str) -> str:
        """获取会话的友好显示名称 [yyyy-mm-dd hh:mm bean_name]"""
        def _get(conn):
            row = conn.execute(
                "SELECT rs.roast_date, rs.roast_time, rs.notes, "
                "b.name AS bean_name "
                "FROM roast_session rs "
                "LEFT JOIN bean b ON rs.bean_id = b.id "
                "WHERE rs.session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return session_id
            parts = [p for p in (row['roast_date'] or '',
                                 row['roast_time'] or '',
                                 row['bean_name'] or '') if p]
            return f"[{' '.join(parts)}]" if parts else (row['notes'] or session_id)
        return execute_with_lock(self.db_path, _get)


def next_session_id(db_path: str) -> str:
    """生成下一个自增 session_id（线程安全）"""
    def _get(conn):
        row = conn.execute(
            "SELECT COALESCE(MAX(CAST(session_id AS INTEGER)), 0) + 1 "
            "FROM roast_session"
        ).fetchone()
        return str(row[0])
    return execute_with_lock(db_path, _get)
