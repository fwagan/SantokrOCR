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

# 参与「发展时间」判定的事件（一爆开始 / 一爆结束 / 烘焙结束）
_DEV_TIME_EVENT_TYPES = frozenset(
    {EventType.FC_START, EventType.FC_END, EventType.ROAST_END})


def _development_seconds(sorted_events: list[dict[str, Any]]) -> Optional[float]:
    """理想曲线的发展时间（秒）；不具备条件时返回 None

    仅当曲线含「一爆开始」且【不含】「一爆结束」、且含「烘焙结束」时存在，
    取值 = time(烘焙结束) − time(一爆开始)。曲线记录了一爆结束的常规场景下，
    发展时间不在此展示，故返回 None。

    同类事件取首个（一爆开始/结束/烘焙结束在一条曲线中均唯一，取首个即唯一解）。
    """
    times: dict[str, float] = {}
    for ev in sorted_events:
        ev_type = ev.get('type', '')
        if ev_type in _DEV_TIME_EVENT_TYPES and ev_type not in times:
            times[ev_type] = float(ev.get('time', 0.0))

    if EventType.FC_END in times or EventType.FC_START not in times:
        return None
    roast_end = times.get(EventType.ROAST_END)
    if roast_end is None:
        return None
    return roast_end - times[EventType.FC_START]


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
            'dev_seconds': float | None,  # 仅「烘焙结束」且在无「一爆结束」曲线中非 None（见下）
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

    sorted_events = sorted(events, key=lambda e: float(e.get('time', 0.0)))
    dev_seconds = _development_seconds(sorted_events)

    checkpoints: list[dict[str, Any]] = []
    prev_time: Optional[float] = None
    for ev in sorted_events:
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
            'dev_seconds': dev_seconds if ev_type == EventType.ROAST_END else None,
        })
        prev_time = ev_time

    return checkpoints
