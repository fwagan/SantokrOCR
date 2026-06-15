"""SqliteBeanRepository：基于 SQLite 的咖啡豆信息存储"""

from typing import List, Optional

import logging

from data.sqlite.connection import execute_with_lock
from data.sqlite.schema import ensure_schema
from data.types import BeanRecord

logger = logging.getLogger(__name__)

_COLUMNS = [
    ('id', 'id'),
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
_COL_LIST = ', '.join(c[0] for c in _COLUMNS)
_DICT_TO_DB = {dk: c for c, dk in _COLUMNS}
_DB_TO_DICT = {c: dk for c, dk in _COLUMNS}
# id 列不出现在 INSERT/UPDATE 的 SET 子句中（自增主键）
_SET_COLUMNS = [c for c in _COLUMNS if c[0] != 'id']
_SET_DICT_TO_DB = {dk: c for c, dk in _SET_COLUMNS}

def _bean_to_set_row(bean: BeanRecord) -> dict:
    """生成 INSERT/UPDATE 用的 SET 子句字典（不含 id）"""
    return {_SET_DICT_TO_DB.get(k, k): v for k, v in bean.items() if k in _SET_DICT_TO_DB}

def _row_to_dict(row) -> BeanRecord:
    d: BeanRecord = {  # type: ignore[misc]
        _DB_TO_DICT.get(col, col): row[col] for col in row.keys()
    }
    d['outOfStock'] = bool(d['outOfStock'])
    return d


class SqliteBeanRepository:
    """基于 SQLite 的咖啡豆仓库"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        ensure_schema(db_path)

    def list_all(self, include_deleted: bool = False) -> List[BeanRecord]:
        def _list(conn):
            if include_deleted:
                where = ''
            else:
                where = 'WHERE is_deleted = 0'
            rows = conn.execute(
                f"SELECT {_COL_LIST} FROM bean {where} ORDER BY name"
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
        return execute_with_lock(self.db_path, _list)

    def get_by_name(self, name: str) -> Optional[BeanRecord]:
        def _get(conn):
            row = conn.execute(
                f"SELECT {_COL_LIST} FROM bean WHERE name = ?", (name,)
            ).fetchone()
            return _row_to_dict(row) if row else None
        return execute_with_lock(self.db_path, _get)

    def add(self, bean: BeanRecord) -> None:
        def _add(conn):
            row = _bean_to_set_row(bean)
            cols = ', '.join(row.keys())
            placeholders = ', '.join('?' for _ in row)
            conn.execute(
                f"INSERT INTO bean ({cols}) VALUES ({placeholders})",
                tuple(row.values()),
            )
            conn.commit()
        execute_with_lock(self.db_path, _add)

    def update(self, bean_id: int, bean: BeanRecord) -> bool:
        def _update(conn):
            row = _bean_to_set_row(bean)
            set_clause = ', '.join(f"{k} = ?" for k in row)
            values = list(row.values()) + [bean_id]
            cur = conn.execute(
                f"UPDATE bean SET {set_clause} WHERE id = ?", values
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
