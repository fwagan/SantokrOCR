"""SQLite 连接管理

提供线程安全的单连接工厂，连接启用 WAL 模式和外键约束。
同一 db_path 在整个进程生命周期内复用同一连接。
"""

import os
import sqlite3
import threading
from typing import Optional

import logging

DEFAULT_DB_FILENAME = 'santokr.db'


def get_default_db_path() -> str:
    """获取 SantokrOCR 默认 SQLite 数据库路径"""
    app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
    return os.path.join(app_data, 'SantokrOCR', DEFAULT_DB_FILENAME)

logger = logging.getLogger(__name__)

# 全局连接缓存：db_path -> (connection, lock)
_connections: dict[str, tuple[sqlite3.Connection, threading.Lock]] = {}
_global_lock = threading.Lock()


def get_connection(db_path: str) -> sqlite3.Connection:
    """获取指定数据库的连接（单例，线程安全）

    Args:
        db_path: SQLite 数据库文件路径

    Returns:
        启用了 WAL 模式和外键约束的 sqlite3.Connection
    """
    db_path = os.path.abspath(db_path)

    # 检查是否已有连接
    with _global_lock:
        if db_path in _connections:
            conn, _ = _connections[db_path]
            return conn

        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # 启用 WAL 模式（提高读写并发性能）
        conn.execute("PRAGMA journal_mode=WAL")
        # 启用外键约束
        conn.execute("PRAGMA foreign_keys=ON")
        # 使用更快的同步模式（WAL 模式下安全）
        conn.execute("PRAGMA synchronous=NORMAL")

        conn_lock = threading.Lock()
        _connections[db_path] = (conn, conn_lock)

        logger.info(f"SQLite 连接已创建: {db_path}")
        return conn


def close_connection(db_path: Optional[str] = None) -> None:
    """关闭指定或全部 SQLite 连接"""
    with _global_lock:
        if db_path:
            db_path = os.path.abspath(db_path)
            targets = [db_path] if db_path in _connections else []
        else:
            targets = list(_connections.keys())

        for path in targets:
            conn, _ = _connections.pop(path, (None, None))
            if conn:
                try:
                    conn.close()
                    logger.info(f"SQLite 连接已关闭: {path}")
                except Exception as e:
                    logger.error(f"关闭 SQLite 连接失败: {path}, {e}")


def execute_with_lock(db_path: str, callback):
    """在连接锁的保护下执行数据库操作

    Args:
        db_path: 数据库路径
        callback: Callable[[sqlite3.Connection], T]，接收连接并返回结果

    Returns:
        callback 的返回值

    Note:
        如果 callback 抛出异常，会自动回滚当前事务，
        避免事务悬空在下一次复用同一连接时造成不一致。
    """
    conn = get_connection(db_path)
    with _global_lock:
        conn_lock = _connections[db_path][1]
    with conn_lock:
        try:
            return callback(conn)
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
