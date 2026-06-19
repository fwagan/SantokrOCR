"""
SlogSerializer：.slog 交换格式的序列化/反序列化

.slog 是纯 JSON 格式，包含一次烘焙的完整数据（温度、事件、烘焙信息）。
这是 SantokrOCR 的主要数据交换格式，用于导入/导出烘焙曲线。

格式结构（version=1）：
{
    "version": 1,
    "results": [ResultRecord, ...],
    "events": [EventRecord, ...],
    "heater_initial": 50.0,
    "fan_initial": 80.0,
    "roast_info": { ... }       // 可选
}
"""

import json
import logging
import os

from data.json._utils import atomic_write
from data.types import EventType, RoastSession

logger = logging.getLogger(__name__)

_CURRENT_VERSION = 1
_DEFAULT_HEATER_INITIAL = 60.0
_DEFAULT_FAN_INITIAL = 50.0


def _try_float(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

# 必需事件列表（缺少任一事件时 read() 发出警告）
_REQUIRED_EVENTS = [
    EventType.CHARGE,
    EventType.TURNAROUND,
    EventType.FC_START,
]


class SlogSerializer:
    """.slog 交换格式序列化器"""

    # ── 读取 ──

    @classmethod
    def read(cls, path: str) -> RoastSession:
        """读取 .slog 文件，返回 RoastSession 兼容 dict

        从 roast_info 中提取常用字段（roast_date、roast_time、notes 等）
        并映射到 RoastSession 字段名（density→density_override 等）。
        额外返回 _version（原始版本号）、bean_name。

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: JSON 解析失败或格式异常
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f".slog 文件不存在: {path}")

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f".slog 文件 JSON 解析失败: {e}") from e

        if not isinstance(data, dict):
            raise ValueError(f".slog 文件格式异常: 期望 dict，实际为 {type(data).__name__}")

        version = data.get('version', 0)
        if version < 1:
            logger.warning(f".slog 文件版本过旧 (version={version}): {path}")

        results = data.get('results', [])
        if not isinstance(results, list):
            logger.warning(f".slog 文件 'results' 字段格式异常: {type(results).__name__}")
            results = []

        events = data.get('events', [])
        if not isinstance(events, list):
            logger.warning(f".slog 文件 'events' 字段格式异常: {type(events).__name__}")
            events = []

        roast_info = data.get('roast_info', {})
        if not isinstance(roast_info, dict):
            logger.warning(f".slog 文件 'roast_info' 字段格式异常: {type(roast_info).__name__}")
            roast_info = {}

        session: RoastSession = {
            'session_id': '',
            'is_raw_data': False,
            'is_favorite': False,
            'bean_id': None,
            'heater_initial': data.get('heater_initial', _DEFAULT_HEATER_INITIAL),
            'fan_initial': data.get('fan_initial', _DEFAULT_FAN_INITIAL),
            'roast_date': roast_info.get('roast_date', ''),
            'roast_time': roast_info.get('roast_time', ''),
            'roast_no': roast_info.get('roast_no', ''),
            'roast_total': roast_info.get('roast_total', ''),
            'density_override': _try_float(roast_info.get('density')),
            'moisture_override': _try_float(roast_info.get('moisture')),
            'green_weight': _try_float(roast_info.get('green_weight')),
            'roasted_weight': _try_float(roast_info.get('roasted_weight')),
            'notes': roast_info.get('notes', ''),
            'results': results,
            'events': events,
        }

        cls._check_required_events(session, path)
        session['_version'] = version
        session['bean_name'] = roast_info.get('bean_name', '')
        return session

    @classmethod
    def _check_required_events(cls, session: dict, path: str) -> None:
        """检查必需事件并发出警告（不阻断）"""
        existing_types = {ev['type'] for ev in session['events'] if 'type' in ev}
        missing = [ev for ev in _REQUIRED_EVENTS if ev not in existing_types]
        if missing:
            logger.warning(f".slog 文件缺少必需事件: {path} ({', '.join(missing)})")

    # ── 写入 ──

    @staticmethod
    def write(path: str, session: dict) -> None:
        """将烘焙会话数据写入 .slog 文件

        Args:
            path: 输出文件路径
            session: 符合 RoastSession 结构的 dict，
                    包含 results, events, heater_initial, fan_initial, roast_info（可选）
        """
        payload = {
            'version': _CURRENT_VERSION,
            'results': session.get('results', []),
            'events': session.get('events', []),
            'heater_initial': float(session.get('heater_initial', _DEFAULT_HEATER_INITIAL)),
            'fan_initial': float(session.get('fan_initial', _DEFAULT_FAN_INITIAL)),
        }

        roast_info = session.get('roast_info')
        if roast_info:
            payload['roast_info'] = roast_info

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        atomic_write(path, payload)

        logger.info(f".slog 已写入: {path} ({len(payload['results'])} 条记录, "
                    f"{len(payload['events'])} 个事件)")

    # ── 验证 ──

    @classmethod
    def validate(cls, path: str) -> bool:
        """快速验证文件是否为有效的 .slog 格式

        Returns:
            True 表示格式有效（不保证数据语义正确）
        """
        try:
            session = cls.read(path)
            if not isinstance(session.get('results'), list):
                return False
            if not isinstance(session.get('events'), list):
                return False
            return True
        except Exception:
            return False
