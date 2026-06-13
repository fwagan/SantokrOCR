"""SqliteBeanRepository：基于 SQLite 的咖啡豆信息存储"""

from typing import List, Optional

import logging

from data.sqlite.connection import execute_with_lock, get_default_db_path
from data.sqlite.schema import ensure_schema
from data.types import BeanRecord

logger = logging.getLogger(__name__)

_INSERT_COLS = [
    ('name', 'name'),
    ('variety', 'variety'),
    ('process', 'process'),
    ('origin', 'origin'),
    ('altitude', 'altitude'),
    ('density', 'density'),
    ('moisture', 'moisture'),
    ('season', 'season'),
    ('out_of_stock', 'outOfStock'),
    ('is_deleted', 'isDeleted'),
]
_SELECT_COLS = [('id', 'id')] + _INSERT_COLS
_SELECT_COL_LIST = ', '.join(c[0] for c in _SELECT_COLS)
_DICT_TO_DB = {dk: c for c, dk in _INSERT_COLS}
_DB_TO_DICT = {c: dk for c, dk in _INSERT_COLS}


def _row_to_dict(row) -> BeanRecord:
    d: BeanRecord = {  # type: ignore[misc]
        _DB_TO_DICT.get(col, col): row[col] for col in row.keys()
    }
    d['outOfStock'] = bool(d['outOfStock'])
    return d


def _bean_to_row(bean: BeanRecord) -> dict:
    return {_DICT_TO_DB.get(k, k): v for k, v in bean.items() if k in _DICT_TO_DB}


class SqliteBeanRepository:
    """基于 SQLite 的咖啡豆仓库"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_default_db_path()
        ensure_schema(self.db_path)

    def list_all(self, include_deleted: bool = False) -> List[BeanRecord]:
        def _list(conn):
            if include_deleted:
                where = ''
            else:
                where = 'WHERE is_deleted = 0'
            rows = conn.execute(
                f"SELECT {_SELECT_COL_LIST} FROM bean {where} ORDER BY name"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        return execute_with_lock(self.db_path, _list)

    def get_by_name(self, name: str) -> Optional[BeanRecord]:
        def _get(conn):
            row = conn.execute(
                f"SELECT {_SELECT_COL_LIST} FROM bean WHERE name = ?", (name,)
            ).fetchone()
            return _row_to_dict(row) if row else None
        return execute_with_lock(self.db_path, _get)

    def get_by_id(self, bean_id: int) -> Optional[BeanRecord]:
        """按 ID 查找咖啡豆"""
        def _get(conn):
            row = conn.execute(
                f"SELECT {_SELECT_COL_LIST} FROM bean WHERE id = ?", (bean_id,)
            ).fetchone()
            return _row_to_dict(row) if row else None
        return execute_with_lock(self.db_path, _get)

    def add(self, bean: BeanRecord) -> None:
        def _add(conn):
            row = _bean_to_row(bean)
            cols = ', '.join(row.keys())
            placeholders = ', '.join('?' for _ in row)
            conn.execute(
                f"INSERT INTO bean ({cols}) VALUES ({placeholders})",
                tuple(row.values()),
            )
            conn.commit()
        execute_with_lock(self.db_path, _add)

    def update(self, name: str, bean: BeanRecord) -> bool:
        def _update(conn):
            row = _bean_to_row(bean)
            set_clause = ', '.join(f"{k} = ?" for k in row)
            values = list(row.values()) + [name]
            cur = conn.execute(
                f"UPDATE bean SET {set_clause} WHERE name = ?", values
            )
            conn.commit()
            return cur.rowcount > 0
        return execute_with_lock(self.db_path, _update)

    def delete(self, name: str) -> bool:
        """软删除：标记 is_deleted = 1，不移除行"""
        def _delete(conn):
            cur = conn.execute(
                "UPDATE bean SET is_deleted = 1 WHERE name = ? AND is_deleted = 0",
                (name,),
            )
            conn.commit()
            return cur.rowcount > 0
        return execute_with_lock(self.db_path, _delete)
