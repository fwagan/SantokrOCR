"""SqliteEventRepository：基于 SQLite 的烘焙事件存储"""

from typing import List, Optional

import logging

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
            conn.execute("DELETE FROM event WHERE session_id = ?", (session_id,))
            for ev in events:
                conn.execute(
                    "INSERT INTO event (session_id, type, frame, time, value) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, ev.get('type', ''), ev.get('frame', 0),
                     ev.get('time', 0.0), ev.get('value')),
                )
            conn.commit()
        execute_with_lock(self.db_path, _save)

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
