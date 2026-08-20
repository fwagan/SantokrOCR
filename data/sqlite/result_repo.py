"""SqliteResultRepository：基于 SQLite 的帧温度结果存储"""

import logging
from typing import List, Optional

from data.sqlite.connection import execute_with_lock, get_default_db_path
from data.sqlite.schema import ensure_schema
from data.types import ResultRecord

logger = logging.getLogger(__name__)

_COLUMNS = [
    'frame', 'timestamp', 'original_timestamp', 'time_str', 'timer',
    'temp1_full', 'temp1_normal', 'temp1_faulty_digit', 'temp2',
    'abnormal_category',
]
_COL_LIST = ', '.join(_COLUMNS)
_COL_PLACEHOLDERS = ', '.join('?' for _ in _COLUMNS)


def _result_to_row(result: ResultRecord) -> list:
    return [
        result.get('frame', 0),
        result.get('timestamp', 0.0),
        result.get('original_timestamp'),
        result.get('time_str', ''),
        result.get('timer'),
        result.get('temp1_full', ''),
        result.get('temp1_normal', ''),
        result.get('temp1_faulty_digit', -1),
        result.get('temp2', ''),
        result.get('abnormal_category'),
    ]


def _row_to_result(row) -> ResultRecord:
    return {col: row[col] for col in row.keys()}


class SqliteResultRepository:
    """基于 SQLite 的帧温度结果仓库"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_default_db_path()
        ensure_schema(self.db_path)

    def save(self, session_id: str, results: List[ResultRecord]) -> None:
        def _save(conn):
            self._delete_and_insert(conn, session_id, results)
            conn.commit()
        execute_with_lock(self.db_path, _save)

    def save_with_conn(self, conn, session_id: str, results: List[ResultRecord]) -> None:
        """使用外部连接写入但不提交（用于事务协调）"""
        self._delete_and_insert(conn, session_id, results)

    def _delete_and_insert(self, conn, session_id: str, results: List[ResultRecord]) -> None:
        conn.execute("DELETE FROM result WHERE session_id = ?", (session_id,))
        cols = 'session_id, ' + _COL_LIST
        placeholders = '?, ' + _COL_PLACEHOLDERS
        values_batch = [[session_id] + _result_to_row(r) for r in results]
        conn.executemany(
            f"INSERT INTO result ({cols}) VALUES ({placeholders})",
            values_batch,
        )

    def load(self, session_id: str) -> Optional[List[ResultRecord]]:
        def _load(conn):
            rows = conn.execute(
                f"SELECT {_COL_LIST} FROM result "
                "WHERE session_id = ? ORDER BY frame",
                (session_id,),
            ).fetchall()
            if not rows:
                return None
            return [_row_to_result(r) for r in rows]
        return execute_with_lock(self.db_path, _load)

    def update_single(self, session_id: str, frame: int, **fields) -> None:
        """更新单帧的指定字段"""
        def _update(conn):
            set_clause = ', '.join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [session_id, frame]
            conn.execute(
                f"UPDATE result SET {set_clause} "
                "WHERE session_id = ? AND frame = ?",
                values,
            )
            conn.commit()
        execute_with_lock(self.db_path, _update)

    def delete_frames(self, session_id: str, frames: List[int]) -> None:
        """批量删除指定帧"""
        if not frames:
            return

        def _delete(conn):
            placeholders = ', '.join('?' for _ in frames)
            conn.execute(
                f"DELETE FROM result WHERE session_id = ? AND frame IN ({placeholders})",
                [session_id] + list(frames),
            )
            conn.commit()
        execute_with_lock(self.db_path, _delete)

    def delete(self, session_id: str) -> None:
        def _delete(conn):
            conn.execute("DELETE FROM result WHERE session_id = ?", (session_id,))
            conn.commit()
        execute_with_lock(self.db_path, _delete)

    def exists(self, session_id: str) -> bool:
        def _exists(conn):
            row = conn.execute(
                "SELECT 1 FROM result WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
            return row is not None
        return execute_with_lock(self.db_path, _exists)
