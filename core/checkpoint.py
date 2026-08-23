"""
Checkpoint — 基于理想曲线派生 checkpoint 静态列表（纯函数）

输入 ideal_data（CameraRealtimeWindow._build_ideal_data 的输出），
输出 checkpoint 静态条目列表（JSON 可序列化），供 Web 前端一次性拉取。

达成状态不在此跟踪——完全由前端自理（spec: 2026-08-14-checkpoint-design）。
"""

from typing import Any, Optional

from data.types import EventType
from utils.numeric import find_nearest_temperature

# manual checkpoint 对应的事件（数值事件）；其余事件均为 auto
_MANUAL_CHECKPOINT_EVENT_TYPES = frozenset({EventType.HEATER_ADJUST, EventType.FAN_ADJUST})

# 理想曲线必须包含的核心事件（防御性校验，缺一拒绝加载）
_REQUIRED_CHECKPOINT_EVENT_TYPES = frozenset({EventType.CHARGE, EventType.TURNAROUND})


def build_checkpoints(ideal_data: Optional[dict[str, Any]]) -> Optional[list[dict[str, Any]]]:
    """从 ideal_data 派生 checkpoint 静态列表

    每个理想曲线事件 → 一条 checkpoint，按事件 time 升序。
    校验：事件集合必须包含 入豆 + 回温，否则返回 None（调用方拒绝加载曲线）。

    Returns:
        列表（每个元素见下），缺核心事件 / 无数据时返回 None。
        元素：{
            'type': 'auto' | 'manual',
            'event': str,
            'temp': float | None,     # smooth_temp1 上离事件时刻最近点的温度（1 位小数）
            'value': str,             # 入豆=火力/风门初始值；调整=百分比；其余 ''
            'delta': float | None,   # 与上一 checkpoint 的理想时间差（秒），首条为 None
        }
    """
    if not ideal_data:
        return None
    events = ideal_data.get('events') or []
    if not events:
        return None

    event_types = {ev.get('type') for ev in events}
    if not _REQUIRED_CHECKPOINT_EVENT_TYPES.issubset(event_types):
        return None

    resampled_time = ideal_data.get('resampled_time')
    smooth_temp1 = ideal_data.get('smooth_temp1')
    heater_initial = ideal_data.get('heater_initial')
    fan_initial = ideal_data.get('fan_initial')

    checkpoints: list[dict[str, Any]] = []
    prev_time: Optional[float] = None
    for ev in sorted(events, key=lambda e: float(e.get('time', 0.0))):
        ev_type = ev.get('type', '')
        ev_time = float(ev.get('time', 0.0))

        delta = (ev_time - prev_time) if prev_time is not None else None

        if ev_type == EventType.CHARGE:
            value = f"火力: {int(heater_initial or 0)}%  风门: {int(fan_initial or 0)}%"
        elif ev_type in _MANUAL_CHECKPOINT_EVENT_TYPES:
            ev_value = ev.get('value')
            value = f"{int(ev_value)}%" if ev_value is not None else ''
        else:
            value = ''

        ev_temp = find_nearest_temperature(resampled_time, smooth_temp1, ev_time)
        checkpoints.append({
            'type': 'manual' if ev_type in _MANUAL_CHECKPOINT_EVENT_TYPES else 'auto',
            'event': ev_type,
            'temp': round(ev_temp, 1) if ev_temp is not None else None,
            'value': value,
            'delta': delta,
        })
        prev_time = ev_time

    return checkpoints
