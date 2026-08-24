"""
TemperatureDataSource — 温度数据源抽象接口

定义实时温度读取的统一契约，供 ModbusReader 实现。
"""

from abc import ABC, abstractmethod

from utils.signal import Signal


class TemperatureDataSource(ABC):
    """温度数据源的抽象接口

    实现约定：
    - start() 启动后台读取线程，开始 emit result_signal
    - stop() 停止线程，emit finished_signal
    - pause() / resume() 暂停/继续采集
    - is_paused() / is_stopped() 查询状态

    信号：
    - result_signal: emit(result_dict) — 每次成功读取到温度时触发
    - status_signal: emit(message)    — 状态消息
    - finished_signal: emit(success_bool, message) — 线程结束时触发
    """

    # ── 信号 ──
    result_signal: Signal
    status_signal: Signal
    finished_signal: Signal

    # ── 生命周期 ──

    @abstractmethod
    def start(self):
        """启动数据源（创建后台线程）"""

    @abstractmethod
    def stop(self):
        """停止数据源"""

    @abstractmethod
    def pause(self):
        """暂停采集"""

    @abstractmethod
    def resume(self):
        """恢复采集"""

    # ── 查询 ──

    @abstractmethod
    def is_paused(self) -> bool:
        """返回是否处于暂停状态"""

    @abstractmethod
    def is_stopped(self) -> bool:
        """返回是否已停止"""

    # ── 可选重写 ──

    def reset_temperature_tracking(self) -> None:
        """重置温差异常检测状态（默认空实现）"""
