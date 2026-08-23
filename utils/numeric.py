"""数值/时间序列工具函数"""

from typing import Any, Optional

import numpy as np


def find_nearest_temperature(
    times: Any, temps: Any, query_time: float
) -> Optional[float]:
    """在 (times, temps) 曲线上，返回 query_time 时刻（最近采样点）的温度

    最近点语义：取 |times - query_time| 最小下标处的温度，不插值。
    任一输入为 None / 空 / 两数组长度不等时返回 None。

    Args:
        times:       曲线采样点时间轴，长度须与 temps 一致
        temps:       各采样点的温度值（如 smooth_temp1）
        query_time:  要查询温度的时刻（事件 time）

    Returns:
        温度 float（不舍入），数据不足时 None。
    """
    if (
        times is None
        or temps is None
        or len(times) == 0
        or len(temps) == 0
        or len(times) != len(temps)
    ):
        return None
    idx = int(np.abs(np.asarray(times, dtype=float) - query_time).argmin())
    if idx >= len(temps):
        return None
    return float(temps[idx])
