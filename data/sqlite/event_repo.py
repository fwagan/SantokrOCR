"""SqliteEventRepository：基于 SQLite 的烘焙事件存储"""

import logging
from typing import List, Optional

from data.sqlite.connection import execute_with_lock, get_default_db_path
from data.sqlite.schema import ensure_schema
from data.types import EventRecord

logger = logging.getLogger(__name__)


class SqliteEventRepository:
    """基于 SQLite 的烘焙事件仓库"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_default_db_path()
        ensure_schema(self.db_path)

    def save(self, session_id: str, events: List[EventRecord]) -> None:
        def _save(conn):
            self._delete_and_insert(conn, session_id, events)
            conn.commit()
        execute_with_lock(self.db_path, _save)

    def save_with_conn(self, conn, session_id: str, events: List[EventRecord]) -> None:
        """使用外部连接写入但不提交（用于事务协调）"""
        self._delete_and_insert(conn, session_id, events)

    def _delete_and_insert(self, conn, session_id: str, events: List[EventRecord]) -> None:
        conn.execute("DELETE FROM event WHERE session_id = ?", (session_id,))
        for ev in events:
            conn.execute(
                "INSERT INTO event (session_id, type, frame, time, value) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, ev.get('type', ''), ev.get('frame', 0),
                 ev.get('time', 0.0), ev.get('value')),
            )

    def load(self, session_id: str) -> Optional[List[EventRecord]]:
        def _load(conn):
            rows = conn.execute(
                "SELECT type, frame, time, value FROM event "
                "WHERE session_id = ? ORDER BY frame",
                (session_id,),
            ).fetchall()
            if not rows:
                return None
            results: List[EventRecord] = [
                {'type': r['type'], 'frame': r['frame'],
                 'time': r['time'], 'value': r['value']}
                for r in rows
            ]
            return results
        return execute_with_lock(self.db_path, _load)

    def add_event(self, session_id: str, event: EventRecord) -> None:
        """添加单个事件"""
        def _add(conn):
            conn.execute(
                "INSERT INTO event (session_id, type, frame, time, value) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, event.get('type', ''), event.get('frame', 0),
                 event.get('time', 0.0), event.get('value')),
            )
            conn.commit()
        execute_with_lock(self.db_path, _add)

    def replace_event(self, session_id: str, old_event: EventRecord, new_event: EventRecord) -> None:
        """原子替换单个事件：同一事务内删除旧事件并插入新事件

        覆盖唯一事件（如入豆/回温）时使用，避免"先删后加"两条独立事务
        之间失败导致的事件丢失窗口。
        """
        def _replace(conn):
            conn.execute(
                "DELETE FROM event WHERE session_id = ? AND type = ? AND frame = ?",
                (session_id, old_event.get('type', ''), old_event.get('frame', 0)),
            )
            conn.execute(
                "INSERT INTO event (session_id, type, frame, time, value) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, new_event.get('type', ''), new_event.get('frame', 0),
                 new_event.get('time', 0.0), new_event.get('value')),
            )
            conn.commit()
        execute_with_lock(self.db_path, _replace)

    def delete_event(self, session_id: str, event_type: str, frame: int) -> None:
        """删除单个事件"""
        def _delete(conn):
            conn.execute(
                "DELETE FROM event WHERE session_id = ? AND type = ? AND frame = ?",
                (session_id, event_type, frame),
            )
            conn.commit()
        execute_with_lock(self.db_path, _delete)

    def delete(self, session_id: str) -> None:
        def _delete(conn):
            conn.execute("DELETE FROM event WHERE session_id = ?", (session_id,))
            conn.commit()
        execute_with_lock(self.db_path, _delete)

    def exists(self, session_id: str) -> bool:
        def _exists(conn):
            row = conn.execute(
                "SELECT 1 FROM event WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
            return row is not None
        return execute_with_lock(self.db_path, _exists)
