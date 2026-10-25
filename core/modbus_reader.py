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

断连恢复：
  - 连续通信失败达到 _COMM_FAILURE_THRESHOLD 后关闭 client，进入重连状态
  - 重连周期 = max(interval, _MIN_RECONNECT_INTERVAL)，次数不设上限
  - 断连期间仍按采样间隔 emit 占位帧（comm_disconnected=True），
    供 UI 更新 latest_result / 指示灯，但不写入结果表
  - 通信中断不 emit finished_signal，避免重置烘焙状态机
"""

import logging
import threading
import time
from typing import Optional, Tuple

from utils.signal import Signal

from .temperature_source import TemperatureDataSource

logger = logging.getLogger(__name__)

_PAUSE_CHECK_INTERVAL = 0.1          # 暂停循环检查间隔
_MIN_RECONNECT_INTERVAL = 0.5        # 重连尝试最小间隔（秒）
_COMM_FAILURE_THRESHOLD = 3          # 连续通信失败多少次判定连接断开

_PYMODBUS_LOGGER = logging.getLogger("pymodbus.logging")
_PYMODBUS_SAVED_LEVEL: Optional[int] = None


def _set_pymodbus_quiet(quiet: bool) -> None:
    """断连期间屏蔽 pymodbus 内部的逐次错误日志，恢复后还原原级别"""
    global _PYMODBUS_SAVED_LEVEL
    if quiet:
        if _PYMODBUS_SAVED_LEVEL is None:
            _PYMODBUS_SAVED_LEVEL = _PYMODBUS_LOGGER.level
        _PYMODBUS_LOGGER.setLevel(logging.CRITICAL)
    elif _PYMODBUS_SAVED_LEVEL is not None:
        _PYMODBUS_LOGGER.setLevel(_PYMODBUS_SAVED_LEVEL)
        _PYMODBUS_SAVED_LEVEL = None


class ModbusReader(TemperatureDataSource):
    """Modbus 温度读取器

    在后台线程中间隔读取 Modbus 设备温度，通过 Signal 发射 result_dict。
    支持 pause/resume/stop 控制；通信中断后自动无限重连。

    Args:
        temp1_config: 豆温通道设备配置 dict
        temp2_config: 风温通道设备配置 dict（可选，None 表示未激活）
        interval: 采样间隔（秒），同时决定重连周期
    """

    def __init__(self, temp1_config: dict,
                 temp2_config: Optional[dict] = None,
                 interval: float = 1.0):
        super().__init__()

        self._temp1_config = temp1_config
        self._temp2_config = temp2_config
        self.interval = interval
        self._reconnect_interval = max(interval, _MIN_RECONNECT_INTERVAL)

        # 内部状态
        self._client1 = None   # temp1 的 pymodbus client
        self._client2 = None   # temp2 的 pymodbus client（预留）
        self._client_lock = threading.Lock()

        # 信号
        self.result_signal = Signal()
        self.status_signal = Signal()
        self.finished_signal = Signal()

        # 控制
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始非暂停

        # 重连状态
        self._comm_failures = 0
        self._next_reconnect_at = 0.0
        self._disconnected = False

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
        self._comm_failures = 0
        self._next_reconnect_at = 0.0
        self._disconnected = False
        thread = threading.Thread(target=self._run_loop, daemon=True,
                                  name="ModbusReader")
        thread.start()
        self.status_signal.emit("温度读取器已启动")

    def stop(self):
        """停止读取线程（非阻塞：串口关闭放到 daemon 线程）"""
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        _set_pymodbus_quiet(False)
        threading.Thread(target=self._close_clients, daemon=True,
                         name="ModbusReaderClose").start()
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

    # ── 内部方法 ──

    def _run_loop(self):
        """后台主循环"""
        try:
            self._resolve_initial_port()
            if self._connect():
                self.status_signal.emit("温度读取器已连接")
            else:
                self._enter_disconnected(initial=True)
                self._next_reconnect_at = (
                    time.time() + self._reconnect_interval
                )

            while not self._stop_event.is_set():
                # 暂停处理
                while (not self._pause_event.is_set()
                       and not self._stop_event.is_set()):
                    self._stop_event.wait(_PAUSE_CHECK_INTERVAL)

                if self._stop_event.is_set():
                    break

                loop_start = time.time()
                timestamp = time.time() - self._start_time

                # 未连接：到点后尝试重建（仅保存端口，不扫描）
                # 建连成功只代表串口可打开；等首个读取成功再宣告恢复
                if (self._client1 is None
                        and not self._stop_event.is_set()
                        and time.time() >= self._next_reconnect_at):
                    if not self._connect():
                        self._next_reconnect_at = (
                            time.time() + self._reconnect_interval
                        )

                if self._client1 is None:
                    result = self._build_result(timestamp, None)
                    result['comm_disconnected'] = True
                else:
                    value, comm_ok = self._read_temperature(
                        self._client1, self._temp1_config
                    )
                    if comm_ok:
                        self._comm_failures = 0
                        if self._disconnected:
                            self._on_reconnected()
                        result = self._build_result(timestamp, value)
                    else:
                        self._comm_failures += 1
                        if self._comm_failures >= _COMM_FAILURE_THRESHOLD:
                            self._close_client()
                            self._enter_disconnected()
                            self._next_reconnect_at = (
                                time.time() + self._reconnect_interval
                            )
                        result = self._build_result(timestamp, None)
                        result['comm_disconnected'] = True

                self.result_signal.emit(result)
                self._frame_count += 1

                # 等待到下一个采样间隔（stop 可立即打断）
                elapsed = time.time() - loop_start
                sleep_time = max(0, self.interval - elapsed)
                if sleep_time > 0:
                    self._stop_event.wait(sleep_time)

            self.finished_signal.emit(False, "温度读取已停止")

        except Exception as e:
            logger.exception("温度读取线程异常")
            self.status_signal.emit(f"温度读取错误: {e}")
            self.finished_signal.emit(False, f"温度读取错误: {e}")
        finally:
            _set_pymodbus_quiet(False)
            self._close_clients()

    def _resolve_initial_port(self):
        """启动时解析设备端口（设备可能已换口）"""
        # 端口更新由 UI 线程的预览轮询负责写入 YAML，
        # 后台线程只使用 resolve 结果，不写配置。
        from core.modbus_config import resolve_device_port
        port = resolve_device_port(self._temp1_config)
        if port and port != self._temp1_config.get('port', ''):
            self._temp1_config['port'] = port

    def _connect(self) -> bool:
        """关闭旧 client，并用保存的端口重建连接"""
        self._close_client()
        client = self._create_client(self._temp1_config)
        if client is None:
            return False
        with self._client_lock:
            self._client1 = client
        return True

    def _enter_disconnected(self, initial: bool = False) -> None:
        """进入断开状态；只在状态转换时提示一次"""
        self._comm_failures = 0
        if self._disconnected:
            return
        self._disconnected = True
        _set_pymodbus_quiet(True)
        if initial:
            logger.warning("温度读取器未连接，正在重试")
            self.status_signal.emit("温度读取器未连接，正在重试")
        else:
            logger.warning("温度读取器连接断开，正在重连")
            self.status_signal.emit("温度读取器连接断开，正在重连")

    def _on_reconnected(self) -> None:
        """重连成功；只在状态转换时提示一次"""
        self._comm_failures = 0
        self._disconnected = False
        _set_pymodbus_quiet(False)
        logger.warning("温度读取器已重新连接")
        self.status_signal.emit("温度读取器已重新连接")

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
            client.close()
            return None
        except Exception as e:
            # 无限重连期间逐次只记 debug，断连/恢复由状态转换各记一条
            logger.debug(f"创建 Modbus 客户端失败: {e}")
            return None

    def _read_temperature(self, client,
                          config: dict) -> Tuple[Optional[float], bool]:
        """读取一次温度，返回 (温度值或 None, 通信是否成功)

        探头断开 0x7FFF 视为通信成功（链路正常、无温度）；
        异常/错误响应视为通信失败，由调用方累计后触发重连。
        """
        if client is None:
            return None, False

        try:
            slave_id = config.get('slave_id', 1)
            register = config.get('register', 0)
            result = client.read_input_registers(
                register, count=1, device_id=slave_id
            )

            if result is None or result.isError():
                return None, False

            raw = result.registers[0]

            # 探头断开
            if raw == 0x7FFF:
                return None, True

            # signed int16 × 10 换算
            if raw < 32768:
                return raw / 10.0, True
            return (raw - 65536) / 10.0, True

        except Exception as e:
            logger.debug(f"温度读取失败: {e}")
            return None, False

    def _build_result(self, timestamp: float,
                      temp1_value: Optional[float],
                      temp2_value: Optional[float] = None) -> dict:
        """构建统一的 result_dict"""
        if temp1_value is not None:
            temp1_full = f"{temp1_value:.1f}"
        else:
            temp1_full = "????"

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
            'comm_disconnected': False,
        }

    def _close_client(self):
        """关闭并清空 temp1 client"""
        with self._client_lock:
            client, self._client1 = self._client1, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _close_clients(self):
        """关闭所有 Modbus 客户端连接（含预留的 temp2）"""
        self._close_client()
        with self._client_lock:
            client2, self._client2 = self._client2, None
        if client2 is not None:
            try:
                client2.close()
            except Exception:
                pass
