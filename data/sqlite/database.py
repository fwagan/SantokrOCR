"""Database 单例——管理 SQLite 连接和所有 Repository"""

import os
from typing import Optional

from data.sqlite.schema import ensure_schema
from data.sqlite.connection import close_connection
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.event_repo import SqliteEventRepository
from data.sqlite.bean_repo import SqliteBeanRepository


class Database:
    """SQLite 数据库入口，管理所有 Repository（单例）"""

    _instance: Optional['Database'] = None

    def __new__(cls, db_path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init(db_path)
        return cls._instance

    def _init(self, db_path: Optional[str] = None):
        if not db_path:
            app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
            db_path = os.path.join(app_data, 'SantokrOCR', 'santokr.db')
        self.db_path = db_path
        ensure_schema(db_path)
        self.bean = SqliteBeanRepository(db_path)
        self.session = SqliteSessionRepository(db_path)
        self.result = SqliteResultRepository(db_path)
        self.event = SqliteEventRepository(db_path)

    @classmethod
    def reset(cls):
        """清空单例（仅测试用）"""
        if cls._instance:
            close_connection(cls._instance.db_path)
        cls._instance = None
