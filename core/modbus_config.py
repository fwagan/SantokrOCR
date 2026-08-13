"""
Modbus 设备配置管理

加载/保存 `config/modbus_devices.yaml`，自动扫描 COM 口，校验设备连接。
"""

import os
import time
import logging
from typing import Optional, Dict, Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config')
_CONFIG_PATH = os.path.join(_CONFIG_DIR, 'modbus_devices.yaml')

_DEFAULT_CONFIG = {
    'channels': {
        'temp1': {
            'label': '豆温',
            'enabled': False,
            'port': '',
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',
            'stopbits': 1,
            'slave_id': 1,
            'register': 0,
            'data_format': 'int16_x10',
        },
        'temp2': {
            'label': '风温',
            'enabled': False,
            'port': '',
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',
            'stopbits': 1,
            'slave_id': 2,
            'register': 0,
            'data_format': 'int16_x10',
        },
    },
}


def _ensure_config_dir():
    """确保配置目录存在"""
    os.makedirs(_CONFIG_DIR, exist_ok=True)


def load_modbus_config() -> dict:
    """加载 Modbus 设备配置，不存在时返回默认配置"""
    if not os.path.exists(_CONFIG_PATH):
        return dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        return cfg
    except Exception as e:
        logger.warning(f"加载 Modbus 配置失败: {e}")
        return dict(_DEFAULT_CONFIG)


def save_modbus_config(cfg: dict) -> bool:
    """保存 Modbus 设备配置"""
    _ensure_config_dir()
    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            yaml.safe_dump(cfg, f, default_flow_style=False, allow_unicode=True)
        return True
    except Exception as e:
        logger.error(f"保存 Modbus 配置失败: {e}")
        return False


def scan_com_ports(timeout_per_port: float = 0.5) -> list:
    """扫描当前可用 COM 口，返回端口名列表 ['COM1', 'COM3', ...]"""
    import serial.tools.list_ports
    ports = serial.tools.list_ports.comports()
    return sorted([p.device for p in ports])


def probe_device(port: str, slave_id: int = 1, register: int = 0,
                  baudrate: int = 9600, timeout: float = 0.5) -> Optional[float]:
    """探测指定端口上是否存在 Modbus 温度设备

    发送 Read Input Registers (功能码 04) 命令，读取温度寄存器。
    成功返回温度值（℃），失败返回 None。

    Args:
        port: COM 端口名
        slave_id: 从站地址
        register: 寄存器地址
        baudrate: 波特率
        timeout: 超时秒数

    Returns:
        温度值（℃）或 None
    """
    client = None
    try:
        from pymodbus.client import ModbusSerialClient
        client = ModbusSerialClient(
            port=port, baudrate=baudrate,
            bytesize=8, parity='N', stopbits=1,
            timeout=timeout,
        )
        if not client.connect():
            return None

        result = client.read_input_registers(register, count=1, device_id=slave_id)

        if result is None or result.isError():
            return None

        raw = result.registers[0]
        if raw == 0x7FFF:
            return None  # 探头断开

        # signed int16 × 10 换算
        if raw < 32768:
            return raw / 10.0
        else:
            return (raw - 65536) / 10.0

    except Exception as e:
        logger.debug(f"探针 {port} 从站 {slave_id} 失败: {e}")
        return None

    finally:
        # 所有路径（connect 失败 / read 异常 / 错误结果 / 正常返回）都确保关闭串口，避免句柄泄漏
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def resolve_device_port(channel_config: dict, timeout: float = 0.5) -> Optional[str]:
    """找到已配置通道设备当前的 COM 口（自动适应端口变化）

    策略：
    1. 快路径：试保存的 port（用通道的 slave_id/register/baudrate）
    2. 慢路径：枚举所有 COM 口，用通道参数逐口试探
    3. 返回找到的端口名，或 None

    Note:
        未来接入第二台温度读取器后，需要给两台设备分配不同的
        从站地址（slave_id），此时扫描可精确按地址匹配。
        开关量模块（不支持 FC 04）会被 probe_device 自然过滤。
    """
    port = channel_config.get('port', '')
    slave_id = channel_config.get('slave_id', 1)
    register = channel_config.get('register', 0)
    baudrate = channel_config.get('baudrate', 9600)

    # 快路径：试保存的端口
    if port:
        temp = probe_device(port, slave_id=slave_id, register=register,
                            baudrate=baudrate, timeout=timeout)
        if temp is not None:
            return port

    # 慢路径：扫描所有 COM 口
    logger.info(f"端口 {port} 不通，开始扫描...")
    for p in scan_com_ports():
        if p == port:
            continue  # 快路径已试过
        temp = probe_device(p, slave_id=slave_id, register=register,
                            baudrate=baudrate, timeout=timeout)
        if temp is not None:
            logger.info(f"设备已从 {port} 移到 {p}")
            return p

    return None


def auto_detect_device(timeout_per_port: float = 0.5) -> Optional[dict]:
    """自动扫描 COM 口，尝试连接 Modbus 温度设备

    扫描策略：
    1. 枚举所有 COM 口
    2. 对每个端口以默认参数试探（9600 8N1，从站 1，寄存器 0）
    3. 返回第一个成功探测到的设备配置

    Returns:
        设备配置 dict（含 port, slave_id, register, baudrate 等），
        或 None（未发现设备）
    """
    ports = scan_com_ports()
    logger.info(f"扫描到 COM 口: {ports}")

    for port in ports:
        temp = probe_device(port, timeout=timeout_per_port)
        if temp is not None:
            logger.info(f"在 {port} 发现温度读取器: {temp:.1f}℃")
            return {
                'label': '豆温',
                'enabled': True,
                'port': port,
                'baudrate': 9600,
                'bytesize': 8,
                'parity': 'N',
                'stopbits': 1,
                'slave_id': 1,
                'register': 0,
                'data_format': 'int16_x10',
            }
    return None
