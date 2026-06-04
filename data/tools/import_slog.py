"""
.slog → SQLite 导入工具

用法:
    python -m data.tools.import_slog <slog_file> [--db <db_path>]

默认数据库: %APPDATA%/SantokrOCR/santokr.db
"""

import argparse
import os
import sys
from typing import Optional

# 确保能从项目根目录导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from data.serializers.slog import SlogSerializer
from data.sqlite.schema import ensure_schema
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.event_repo import SqliteEventRepository
from data.sqlite.bean_repo import SqliteBeanRepository


def _next_session_id(db_path: str) -> str:
    """从数据库获取下一个自增 session_id"""
    from data.sqlite.connection import get_connection
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(session_id AS INTEGER)), 0) + 1 FROM roast_session"
    ).fetchone()
    return str(row[0])


def _build_roast_session(data: dict, session_id: str) -> dict:
    """将 .slog 数据转为烘焙会话记录"""
    ri = data.get('roast_info') or {}
    return {
        'session_id': session_id,
        'is_raw_data': False,
        'heater_initial': data.get('heater_initial', 60.0),
        'fan_initial': data.get('fan_initial', 50.0),
        'density_override': _try_float(ri.get('density')),
        'moisture_override': _try_float(ri.get('moisture')),
        'roast_date': ri.get('roast_date', ''),
        'roast_time': ri.get('roast_time', ''),
        'roast_no': ri.get('roast_no', ''),
        'roast_total': ri.get('roast_total', ''),
        'green_weight': _try_float(ri.get('green_weight')),
        'roasted_weight': _try_float(ri.get('roasted_weight')),
        'notes': ri.get('notes', ''),
    }


def _try_float(v) -> Optional[float]:
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def import_slog(slog_path: str, db_path: str) -> str:
    """导入 .slog 文件到 SQLite 数据库"""
    if not os.path.exists(slog_path):
        raise FileNotFoundError(f".slog 文件不存在: {slog_path}")

    ensure_schema(db_path)
    session_id = _next_session_id(db_path)

    data = SlogSerializer.read(slog_path)
    session = _build_roast_session(data, session_id)

    sr = SqliteSessionRepository(db_path)
    rr = SqliteResultRepository(db_path)
    er = SqliteEventRepository(db_path)

    sr.save(session_id, session)
    results = data.get('results', [])
    if results:
        rr.save(session_id, results)
    events = data.get('events', [])
    if events:
        er.save(session_id, events)

    return session_id


def main():
    parser = argparse.ArgumentParser(description='导入 .slog 文件到 SQLite 数据库')
    parser.add_argument('slog_file', help='.slog 文件路径')
    parser.add_argument('--db', help='SQLite 数据库路径（默认 APPDATA/SantokrOCR/santokr.db）')
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
        db_path = os.path.join(app_data, 'SantokrOCR', 'santokr.db')

    session_id = import_slog(args.slog_file, db_path)
    results_count = len(SlogSerializer.read(args.slog_file).get('results', []))
    events_count = len(SlogSerializer.read(args.slog_file).get('events', []))

    print(f"已导入: session_id={session_id}")
    print(f"  results: {results_count} 条")
    print(f"  events:  {events_count} 条")
    print(f"  数据库:  {db_path}")


if __name__ == '__main__':
    main()
