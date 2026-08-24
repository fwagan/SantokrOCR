"""
ModbusReader — 通过 Modbus RTU 协议读取温度数据

实现 TemperatureDataSource 接口，从 USB Modbus 温度读取器（MAX31865 + PT100）
获取温度值，输出统一的 result_dict（格式见 _build_result）。

设备参数（已确认）：
  - 协议: Modbus RTU (9600 8N1)
  - 功能码: 04 (Read Input Registers)
  - 寄存器: 地址 0
  - 数据格式: signed int16 × 10（读取后 ÷10 得实际温度）
  - 探头断开标志: 0x7FFF (32767)
"""

import logging
import threading
import time
from typing import Callable, Optional

from utils.signal import Signal

from .temperature_source import TemperatureDataSource

logger = logging.getLogger(__name__)

_TEMP_DIFF_THRESHOLD_DEFAULT = 3.0   # 温差异常检测阈值（℃/帧）
_MAX_EFFECTIVE_GAP = 4               # 温差异常最大有效间隔
_PAUSE_CHECK_INTERVAL = 0.1          # 暂停循环检查间隔
_READ_RETRY_INTERVAL = 1.0           # 读取失败后重试间隔（秒）


class ModbusReader(TemperatureDataSource):
    """Modbus 温度读取器

    在后台线程中间隔读取 Modbus 设备温度，通过 Signal 发射 result_dict。
    支持 pause/resume/stop 控制，异常重连。

    Args:
        temp1_config: 豆温通道设备配置 dict
        temp2_config: 风温通道设备配置 dict（可选，None 表示未激活）
        interval: 采样间隔（秒）
        temp_diff_threshold: 温差异常检测阈值（℃/帧）
    """

    def __init__(self, temp1_config: dict,
                 temp2_config: Optional[dict] = None,
                 interval: float = 1.0,
                 temp_diff_threshold: float = _TEMP_DIFF_THRESHOLD_DEFAULT):
        super().__init__()

        self._temp1_config = temp1_config
        self._temp2_config = temp2_config
        self.interval = interval
        self.temp_diff_threshold = temp_diff_threshold

        # 内部状态
        self._client1 = None   # temp1 的 pymodbus client
        self._client2 = None   # temp2 的 pymodbus client（预留）

        # 信号
        self.result_signal = Signal()
        self.status_signal = Signal()
        self.finished_signal = Signal()

        # 控制
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始非暂停

        # 温差异常检测
        self._last_valid_temp1: Optional[float] = None
        self._consecutive_invalid_frames: int = 0

        # 帧计数
        self._frame_count: int = 0
        self._start_time: Optional[float] = None

    # ── TemperatureDataSource 接口 ──

    def start(self):
        """启动后台读取线程"""
        self._stop_event.clear()
        self._pause_event.set()
        self._start_time = time.time()
        self._frame_count = 0
        thread = threading.Thread(target=self._run_loop, daemon=True,
                                  name="ModbusReader")
        thread.start()
        self.status_signal.emit("温度读取器已启动")

    def stop(self):
        """停止读取线程"""
        self._stop_event.set()
        self._close_clients()
        self.status_signal.emit("温度读取器已停止")

    def pause(self):
        """暂停采集"""
        self._pause_event.clear()
        self.status_signal.emit("温度读取已暂停")

    def resume(self):
        """恢复采集"""
        self._pause_event.set()
        self.status_signal.emit("温度读取已恢复")

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def reset_temperature_tracking(self) -> None:
        """重置温差异常检测状态"""
        self._last_valid_temp1 = None
        self._consecutive_invalid_frames = 0

    # ── 内部方法 ──

    def _run_loop(self):
        """后台主循环"""
        try:
            # 端口解析：先试保存的端口，不通则扫描
            # 注意：端口更新由 UI 线程的预览轮询负责写入 YAML，
            # 后台线程只使用 resolve 结果，不写配置。
            from core.modbus_config import resolve_device_port
            port = resolve_device_port(self._temp1_config)
            if port and port != self._temp1_config.get('port', ''):
                self._temp1_config['port'] = port

            self._client1 = self._create_client(self._temp1_config)
            if self._client1 is None:
                self.finished_signal.emit(False, "无法连接温度读取器")
                return

            self.status_signal.emit("温度读取器已连接")

            while not self._stop_event.is_set():
                # 暂停处理
                while not self._pause_event.is_set() and not self._stop_event.is_set():
                    time.sleep(_PAUSE_CHECK_INTERVAL)

                if self._stop_event.is_set():
                    break

                loop_start = time.time()
                timestamp = time.time() - self._start_time

                # 读取 temp1（豆温）
                temp1_value = self._read_temperature(
                    self._client1, self._temp1_config
                )

                # 读取 temp2（预留）
                temp2_value = None

                # 构建 result_dict
                result = self._build_result(timestamp, temp1_value, temp2_value)

                # 温差异常检测
                self._check_anomaly(result)

                self.result_signal.emit(result)
                self._frame_count += 1

                # 等待到下一个采样间隔
                elapsed = time.time() - loop_start
                sleep_time = max(0, self.interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            self.finished_signal.emit(False, "温度读取已停止")

        except Exception as e:
            logger.exception("温度读取线程异常")
            self.status_signal.emit(f"温度读取错误: {e}")
            self.finished_signal.emit(False, f"温度读取错误: {e}")
        finally:
            self._close_clients()

    def _create_client(self, config: dict):
        """创建并连接 Modbus 串口客户端"""
        if not config or not config.get('enabled', False):
            return None

        try:
            from pymodbus.client import ModbusSerialClient
            client = ModbusSerialClient(
                port=config['port'],
                baudrate=config.get('baudrate', 9600),
                bytesize=config.get('bytesize', 8),
                parity=config.get('parity', 'N'),
                stopbits=config.get('stopbits', 1),
                timeout=0.5,
            )
            if client.connect():
                return client
            else:
                client.close()
                return None
        except Exception as e:
            logger.error(f"创建 Modbus 客户端失败: {e}")
            return None

    def _read_temperature(self, client, config: dict) -> Optional[float]:
        """从 Modbus 设备读取温度值

        发送功能码 04 (Read Input Registers)，读取指定寄存器。
        返回温度值（℃），读取失败或探头断开返回 None。

        Args:
            client: pymodbus ModbusSerialClient 实例
            config: 设备配置 dict（含 slave_id, register）

        Returns:
            温度值（℃）或 None（读取失败/探头断开）
        """
        if client is None:
            return None

        try:
            slave_id = config.get('slave_id', 1)
            register = config.get('register', 0)
            result = client.read_input_registers(register, count=1, device_id=slave_id)

            if result is None or result.isError():
                return None

            raw = result.registers[0]

            # 探头断开
            if raw == 0x7FFF:
                return None

            # signed int16 × 10 换算
            if raw < 32768:
                return raw / 10.0
            else:
                return (raw - 65536) / 10.0

        except Exception as e:
            logger.warning(f"温度读取失败: {e}")
            return None

    def _build_result(self, timestamp: float,
                      temp1_value: Optional[float],
                      temp2_value: Optional[float]) -> dict:
        """构建统一的 result_dict"""
        # temp1 格式化
        if temp1_value is not None:
            temp1_full = f"{temp1_value:.1f}"
        else:
            temp1_full = "????"

        # temp2 格式化（预留通道，暂时 hardcode 0.0）
        if temp2_value is not None:
            temp2_text = f"{temp2_value:.1f}"
        else:
            temp2_text = "0.0"

        return {
            'frame': self._frame_count,
            'timestamp': round(timestamp, 3),
            'original_timestamp': round(timestamp, 3),
            'time_str': (
                f"{int(timestamp // 60):02d}:{int(timestamp % 60):02d}:"
                f"{int((timestamp % 1) * 1000):03d}"
            ),
            'temp1_full': temp1_full,
            'temp1_normal': '',
            'temp1_faulty_digit': -9,   # -9 标记"非 OCR 来源"
            'temp2': temp2_text,
            'abnormal_category': None,
        }

    def _check_anomaly(self, result: dict):
        """温差异常检测（帧率归一化：连续无效帧越多，允许温差越大）"""
        try:
            curr_temp = float(result['temp1_full'])
            if self._last_valid_temp1 is not None:
                gap = min(self._consecutive_invalid_frames + 1, _MAX_EFFECTIVE_GAP)
                if abs(curr_temp - self._last_valid_temp1) > gap * self.temp_diff_threshold:
                    result['abnormal_category'] = 'temperature_diff'
                else:
                    self._last_valid_temp1 = curr_temp
                    self._consecutive_invalid_frames = 0
            else:
                self._last_valid_temp1 = curr_temp
        except (ValueError, TypeError):
            self._consecutive_invalid_frames += 1

    def _close_clients(self):
        """关闭所有 Modbus 客户端连接"""
        for client in (self._client1, self._client2):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        self._client1 = None
        self._client2 = None
