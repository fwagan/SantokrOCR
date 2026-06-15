"""
.slog → SQLite 持续导入工具

持续运行，输入 .slog 文件路径即可导入到数据库。
数据库路径自动检测: %APPDATA%/SantokrOCR/santokr.db
输入空行或 q 退出。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from data.serializers.slog import SlogSerializer
from data.sqlite.schema import ensure_schema
from data.sqlite.session_repo import SqliteSessionRepository
from data.sqlite.result_repo import SqliteResultRepository
from data.sqlite.event_repo import SqliteEventRepository


def _get_db_path() -> str:
    app_data = os.environ.get('APPDATA', os.path.expanduser('~/.local/share'))
    return os.path.join(app_data, 'SantokrOCR', 'santokr.db')


def _build_roast_session(data: dict, session_id: str) -> dict:
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


def _try_float(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def import_slog(slog_path: str, db_path: str) -> str:
    """导入 .slog 文件到 SQLite 数据库，返回 session_id"""
    if not os.path.exists(slog_path):
        raise FileNotFoundError(f".slog 文件不存在: {slog_path}")

    ensure_schema(db_path)
    session_id = next_session_id(db_path)

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
    db_path = _get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print(f"数据库: {db_path}")
    print("输入 .slog 文件路径导入，空行或 q 退出。\n")

    while True:
        raw = input("slog > ").strip()
        if not raw or raw.lower() == 'q':
            print("退出。")
            break

        # 引号包裹时去掉引号（拖拽文件到终端可能带引号）
        slog_path = raw.strip('"\'')
        if not os.path.exists(slog_path):
            print(f"  文件不存在: {slog_path}")
            continue
        if not slog_path.lower().endswith('.slog'):
            print(f"  不是 .slog 文件: {slog_path}")
            continue

        try:
            session_id = import_slog(slog_path, db_path)
            data = SlogSerializer.read(slog_path)
            results_count = len(data.get('results', []))
            events_count = len(data.get('events', []))
            print(f"  已导入: session_id={session_id}, results={results_count}, events={events_count}")
        except Exception as e:
            print(f"  导入失败: {e}")


if __name__ == '__main__':
    main()
