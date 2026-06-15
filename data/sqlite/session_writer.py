"""SessionWriter：原子写入完整烘焙会话

协调三个 Repository 在单个事务中写入 session + results + events，
避免部分写入导致数据不一致。
"""

from typing import List, Optional

from data.sqlite.connection import execute_with_lock, get_connection
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.event_repo import SqliteEventRepository
from data.types import ResultRecord, EventRecord, RoastSession


class SessionWriter:
    """烘焙会话原子写入器"""

    def __init__(
        self,
        db_path: Optional[str] = None,
        session_repo: Optional[SqliteSessionRepository] = None,
        result_repo: Optional[SqliteResultRepository] = None,
        event_repo: Optional[SqliteEventRepository] = None,
    ):
        self.db_path = db_path
        self._sr = session_repo
        self._rr = result_repo
        self._er = event_repo
        if session_repo is not None and db_path is None:
            self.db_path = session_repo.db_path

    def _ensure_repos(self):
        if self._sr is None:
            self._sr = SqliteSessionRepository(self.db_path)
        if self._rr is None:
            self._rr = SqliteResultRepository(self.db_path)
        if self._er is None:
            self._er = SqliteEventRepository(self.db_path)

    def save_full(
        self,
        session_id: str,
        session: RoastSession,
        results: List[ResultRecord],
        events: List[EventRecord],
    ) -> None:
        """原子写入 session + results + events"""
        self._ensure_repos()

        def _atomic(conn):
            self._sr.save_with_conn(conn, session_id, session)
            self._rr.save_with_conn(conn, session_id, results)
            self._er.save_with_conn(conn, session_id, events)
            conn.commit()

        execute_with_lock(self.db_path, _atomic)
