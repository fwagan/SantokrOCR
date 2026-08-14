"""
Checkpoint — 基于理想曲线派生 checkpoint 静态列表（纯函数）

输入 ideal_data（CameraRealtimeWindow._build_ideal_data 的输出），
输出 checkpoint 静态条目列表（JSON 可序列化），供 Web 前端一次性拉取。

达成状态不在此跟踪——完全由前端自理（spec: 2026-08-14-checkpoint-design）。
"""

from typing import Any, Dict, List, Optional

import numpy as np

# manual checkpoint 对应的事件（数值事件）；其余事件均为 auto
_MANUAL_EVENT_TYPES = frozenset({"调整火力", "调整风门"})

# 理想曲线必须包含的核心事件（防御性校验，缺一拒绝加载）
_REQUIRED_EVENT_TYPES = frozenset({"入豆", "回温"})


def build_checkpoints(ideal_data: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
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
            'offset': float | None,   # 与上一 checkpoint 的理想时间差（秒），首条为 None
        }
    """
    if not ideal_data:
        return None
    events = ideal_data.get('events') or []
    if not events:
        return None

    event_types = {ev.get('type') for ev in events}
    if not _REQUIRED_EVENT_TYPES.issubset(event_types):
        return None

    resampled_time = ideal_data.get('resampled_time')
    smooth_temp1 = ideal_data.get('smooth_temp1')
    heater_initial = ideal_data.get('heater_initial')
    fan_initial = ideal_data.get('fan_initial')

    checkpoints: List[Dict[str, Any]] = []
    prev_time: Optional[float] = None
    for ev in sorted(events, key=lambda e: float(e.get('time', 0.0))):
        ev_type = ev.get('type', '')
        ev_time = float(ev.get('time', 0.0))

        offset = (ev_time - prev_time) if prev_time is not None else None

        if ev_type == '入豆':
            value = f"火力: {int(heater_initial or 0)}%  风门: {int(fan_initial or 0)}%"
        elif ev_type in _MANUAL_EVENT_TYPES:
            ev_value = ev.get('value')
            value = f"{int(ev_value)}%" if ev_value is not None else ''
        else:
            value = ''

        checkpoints.append({
            'type': 'manual' if ev_type in _MANUAL_EVENT_TYPES else 'auto',
            'event': ev_type,
            'temp': _find_temp(resampled_time, smooth_temp1, ev_time),
            'value': value,
            'offset': offset,
        })
        prev_time = ev_time

    return checkpoints


def _find_temp(resampled_time, smooth_temp1, ev_time: float) -> Optional[float]:
    """smooth_temp1 上离 ev_time 最近点的温度，四舍五入 1 位；数据不足返回 None"""
    if (resampled_time is None or smooth_temp1 is None
            or len(resampled_time) == 0 or len(smooth_temp1) == 0
            or len(resampled_time) != len(smooth_temp1)):
        return None
    idx = int(np.abs(np.asarray(resampled_time, dtype=float) - ev_time).argmin())
    if idx >= len(smooth_temp1):
        return None
    return round(float(smooth_temp1[idx]), 1)
