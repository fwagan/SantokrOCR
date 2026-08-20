#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Modbus RTU 温度读取器模拟器

在无真实硬件（USB Modbus 温度读取器：CH340 + MAX31865 + PT100）时，
模拟从站响应 SantokrOCR 的温度读取请求，用于测试 app 的 Modbus 链路。

与真实设备一致的协议参数：
  - 协议: Modbus RTU (9600 8N1)
  - 从站地址: 1
  - 功能码: 04 (Read Input Registers)，寄存器地址 0
  - 数据格式: signed int16 × 10（读取后 ÷10 得温度℃）
  - 探头断开标志: 0x7FFF (32767)

温度模拟（默认模拟"回温检测"所需的 V 型曲线）：
  - 默认从 175℃ 开始线性下降（℃/min），控制台按 R（或回车）翻转趋势转为上升
  - 可选高斯噪声、可选探头断开（立即或运行 N 秒后）

使用前提：安装 com0com 创建虚拟串口对（如 COM10 ↔ COM11），
本脚本绑定其中一端（如 COM10），app 的 Modbus 设备配置另一端（COM11）。
详见同目录 README.md。

示例:
  python tools/modbus_mock_server/mock_modbus_server.py --port COM10
  python tools/modbus_mock_server/mock_modbus_server.py --port COM10 --rate 20 --trend up --noise 0.5
"""

import argparse
import asyncio
import random
import socket
import sys
import time
import threading

# pymodbus 3.6+ 的推荐 API（3.13 已验证）。若环境未安装先执行：
#   pip install -r tools/modbus_mock_server/requirements_test.txt
from pymodbus.framer import FramerType
from pymodbus.server import ModbusSerialServer
from pymodbus.simulator import DataType, SimData, SimDevice

try:
    import msvcrt  # Windows 控制台按键检测（翻转趋势用），仅 Windows 可用
except ImportError:
    msvcrt = None  # 非 Windows 环境下无按键翻转功能

# ── 设备协议常量（与真实设备一致，勿改）──
DEFAULT_SLAVE_ID = 1          # 从站地址
TEMP_REGISTER = 0             # 温度所在输入寄存器地址
TEMP_MULTIPLIER = 10          # 寄存器值为温度 × 10
PROBE_DISCONNECT_RAW = 0x7FFF  # 探头断开标志

# ── 串口默认参数 ──
DEFAULT_BAUD = 9600
DEFAULT_BYTESIZE = 8
DEFAULT_PARITY = "N"
DEFAULT_STOPBITS = 1

# ── 温度模拟默认参数 ──
DEFAULT_START_TEMP = 175.0    # 起始温度（℃），默认回温场景的高温起点
DEFAULT_TREND = "down"        # 初始趋势：down=下降，up=上升
DEFAULT_RATE = 10.0           # 趋势变化速率（℃/min），升降同幅
DEFAULT_STEP = 1.0            # 采样步长（秒）
DEFAULT_NOISE = 0.0           # 高斯噪声幅度（℃），0 表示无噪声

# ── 内部参数 ──
_SERVER_START_TIMEOUT = 5.0   # 等待串口监听就绪的超时（秒）
_LISTEN_POLL_INTERVAL = 0.05  # 轮询监听状态间隔（秒）
_SHUTDOWN_TIMEOUT = 5.0       # 等待 server 关闭的超时（秒）


class TemperatureSimulator:
    """烘焙温度模型（V 型：先降后升，模拟回温检测）

    默认从起始温度（175℃）按固定速率线性下降，控制台按 R/回车可翻转趋势
    转为上升（升降速率同幅）。翻转时以当前温度续算，温度不跳变。
    可选高斯噪声与探头断开。温度按"启动以来经过的时间"计算，
    保证控制台显示与寄存器返回一致。
    """

    def __init__(self, start_temp: float, rate_c_per_min: float,
                 noise_amplitude: float = 0.0,
                 probe_disconnect: bool = False,
                 disconnect_after_seconds: float | None = None,
                 initial_rising: bool = False):
        self.start_temp = start_temp
        self.rate_c_per_min = rate_c_per_min
        self.noise_amplitude = noise_amplitude
        self.probe_disconnect = probe_disconnect           # 立即断开
        self.disconnect_after_seconds = disconnect_after_seconds  # 延时断开
        self._rising = initial_rising                      # False=下降, True=上升
        self._base_temp = start_temp                       # 当前趋势段的起始温度
        self._base_time = time.monotonic()                 # 当前趋势段的起始时刻
        self._start_time = self._base_time

    def elapsed(self) -> float:
        """启动以来经过的秒数"""
        return time.monotonic() - self._start_time

    def is_disconnected(self) -> bool:
        """是否处于探头断开状态"""
        if self.probe_disconnect:
            return True
        if (self.disconnect_after_seconds is not None
                and self.elapsed() >= self.disconnect_after_seconds):
            return True
        return False

    def temperature(self) -> float | None:
        """当前模拟温度（℃）；探头断开返回 None"""
        if self.is_disconnected():
            return None
        sign = 1.0 if self._rising else -1.0
        temp = self._base_temp + sign * self.rate_c_per_min * (
            (time.monotonic() - self._base_time) / 60.0)
        if self.noise_amplitude > 0:
            temp += self._noise()
        return temp

    def is_rising(self) -> bool:
        """当前是否上升趋势"""
        return self._rising

    def flip_direction(self) -> bool:
        """翻转趋势方向（降↔升），保持温度连续不跳变；返回翻转后的上升标志"""
        now = time.monotonic()
        sign = 1.0 if self._rising else -1.0
        self._base_temp += sign * self.rate_c_per_min * ((now - self._base_time) / 60.0)
        self._base_time = now
        self._rising = not self._rising
        return self._rising

    def reset(self, start_temp: float, rate_c_per_min: float,
              initial_rising: bool = False) -> None:
        """重置温度模型起始状态（自检复用）"""
        self.start_temp = start_temp
        self.rate_c_per_min = rate_c_per_min
        self._rising = initial_rising
        self._base_temp = start_temp
        self._base_time = time.monotonic()

    def raw_value(self) -> int:
        """寄存器原始值（int16 × 10，无符号形式）；探头断开返回 0x7FFF"""
        temp = self.temperature()
        if temp is None:
            return PROBE_DISCONNECT_RAW
        # & 0xFFFF 把负温度转为 16 位无符号（两补码），保证帧编码合法、
        # app 端按 signed int16 换算后仍为负值
        return int(round(temp * TEMP_MULTIPLIER)) & 0xFFFF

    def _noise(self) -> float:
        """确定性高斯噪声

        以 0.1s 时间窗口为种子，同一窗口内多次调用返回相同噪声，
        保证控制台显示与 app 读到的寄存器值一致（无闪烁跳变）。
        """
        bucket = int(self.elapsed() * 10)
        seed = f"{self._start_time:.3f}:{bucket}"
        return random.Random(seed).gauss(0.0, self.noise_amplitude)


def build_sim_device(sim: TemperatureSimulator, slave_id: int) -> SimDevice:
    """构建 pymodbus 模拟从站

    SimData 放在 shared 寄存器块（地址 0，INT16），功能码 04 读输入寄存器
    会命中该块。SimDevice 的 action 在每次寄存器被访问时被调用，把当前
    模拟温度写回 current_registers[0]，实现"每次读取即最新温度"。

    注：pymodbus 3.13 中旧 datastore（ModbusDeviceContext 等）已弃用，
    这里使用官方推荐的 SimData/SimDevice 新 API。
    """
    async def _on_register_access(function_code, start_address, address,
                                  count, current_registers, set_values):
        current_registers[0] = sim.raw_value()

    return SimDevice(
        id=slave_id,
        simdata=[SimData(address=TEMP_REGISTER, count=1,
                         values=sim.raw_value(), datatype=DataType.INT16)],
        action=_on_register_access,
    )


def make_trace_packet():
    """构造帧追踪回调（--verbose 用），返回原数据不修改"""
    def trace_packet(sending: bool, data: bytes) -> bytes:
        direction = "发送" if sending else "接收"
        print(f"    [帧] {direction} {data.hex(' ')}")
        return data
    return trace_packet


async def wait_listening(serve_task: asyncio.Task, server: ModbusSerialServer,
                         port: str) -> bool:
    """等待串口监听就绪；端口打不开时给出友好错误

    serve_forever 内部先 listen()：成功则设置 server.transport；
    失败（串口不存在/被占用）则任务立刻以 RuntimeError 结束。
    """
    deadline = time.monotonic() + _SERVER_START_TIMEOUT
    while time.monotonic() < deadline:
        if serve_task.done():
            exc = serve_task.exception()
            print(f"错误：无法打开串口 {port}（{exc}）", file=sys.stderr)
            print("请确认已安装 com0com 并创建虚拟串口对，且 --port 使用的是配对中的一端。",
                  file=sys.stderr)
            return False
        if server.transport is not None:
            return True
        await asyncio.sleep(_LISTEN_POLL_INTERVAL)

    # 超时仍未就绪
    if serve_task.done():
        print(f"错误：无法打开串口 {port}（{serve_task.exception()}）", file=sys.stderr)
    else:
        print(f"错误：等待串口 {port} 监听就绪超时", file=sys.stderr)
    return False


def print_banner(args) -> None:
    """打印启动横幅（含 app 侧配置提示）"""
    print("=" * 62)
    print("Modbus RTU 温度读取器模拟器")
    print("=" * 62)
    print(f"  监听串口   : {args.port}（从站 {args.slave_id}，{args.baud} 8N1）")
    print(f"  app 配置   : 设备配置 → temp1 → 端口选配对另一端（com0com 对中的另一个 COM 口）")
    trend_text = "上升" if args.trend == "up" else "下降"
    print(f"  起始温度   : {args.start_temp:.1f} ℃")
    print(f"  变化速率   : {args.rate:.1f} ℃/min（升降同幅）")
    print(f"  初始趋势   : {trend_text}（运行中按 R 或回车翻转趋势）")
    if args.noise > 0:
        print(f"  噪声幅度   : ±{args.noise:.1f} ℃（高斯）")
    if args.probe_disconnect:
        print("  探头状态   : 恒断开（0x7FFF）")
    elif args.disconnect_after is not None:
        print(f"  探头状态   : 运行 {args.disconnect_after:.0f}s 后断开（0x7FFF）")
    else:
        print("  探头状态   : 正常")
    if args.verbose:
        print("  帧追踪     : 开启")
    print("-" * 62)
    print(f"   {'时间':>8} | {'寄存器':>6} | {'温度(℃)':>8} | 趋势")
    print("-" * 62, flush=True)


async def run_server(args) -> int:
    """主协程：启动模拟从站 + 控制台温度循环"""
    if not args.port:
        print("错误：缺少 --port（模拟器绑定的串口名）", file=sys.stderr)
        return 1

    sim = TemperatureSimulator(
        start_temp=args.start_temp,
        rate_c_per_min=args.rate,
        noise_amplitude=args.noise,
        probe_disconnect=args.probe_disconnect,
        disconnect_after_seconds=args.disconnect_after,
        initial_rising=(args.trend == "up"),
    )

    device = build_sim_device(sim, args.slave_id)

    server = ModbusSerialServer(
        device,
        framer=FramerType.RTU,
        port=args.port,
        baudrate=args.baud,
        bytesize=DEFAULT_BYTESIZE,
        parity=DEFAULT_PARITY,
        stopbits=DEFAULT_STOPBITS,
        timeout=1.0,
        trace_packet=make_trace_packet() if args.verbose else None,
    )

    serve_task = asyncio.create_task(server.serve_forever())

    if not await wait_listening(serve_task, server, args.port):
        return 1

    print_banner(args)
    try:
        while True:
            flipped = False
            if msvcrt is not None and msvcrt.kbhit():
                key = msvcrt.getch()
                if key in (b"r", b"R", b"\r"):
                    rising = sim.flip_direction()
                    flipped = True

            raw = sim.raw_value()
            temp = sim.temperature()
            ts = time.strftime("%H:%M:%S")
            trend = "升" if sim.is_rising() else "降"
            if temp is None:
                print(f"   {ts:>8} | {raw:6d} |  探头断开 | --", flush=True)
            else:
                print(f"   {ts:>8} | {raw:6d} | {temp:8.1f} | {trend}", flush=True)
            if flipped:
                print(f"   >>> 已翻转趋势：现在{'上升' if rising else '下降'}（回温检测点）",
                      flush=True)
            await asyncio.sleep(args.step)
    finally:
        # Ctrl+C（asyncio.run 取消主任务）时走这里做优雅退出
        await server.shutdown()
        await asyncio.wait_for(serve_task, timeout=_SHUTDOWN_TIMEOUT)
    return 0


def run_self_test(args) -> int:
    """无串口自检：通过 TCP 回环走完整 server→client 链路

    不依赖 com0com，用 app 同款客户端调用（read_input_registers，功能码 04）
    验证：温度递增、探头断开(0x7FFF)、负温度换算。输出与真实 Modbus 请求一致。
    """
    from pymodbus.client import ModbusTcpClient
    from pymodbus.server import ModbusTcpServer

    # 申请一个临时空闲端口，避免与现有服务冲突
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    sim = TemperatureSimulator(
        start_temp=args.start_temp,
        rate_c_per_min=args.rate,
        noise_amplitude=0.0,      # 自检关闭噪声，保证断言稳定
        initial_rising=True,      # 自检"正常升温"用例需要上升趋势
    )
    device = build_sim_device(sim, args.slave_id)
    server = None
    server_loop = None

    def server_thread():
        nonlocal server, server_loop
        async def runner():
            nonlocal server, server_loop
            server_loop = asyncio.get_running_loop()
            server = ModbusTcpServer(device, address=("127.0.0.1", port))
            await server.serve_forever()
        asyncio.run(runner())

    thread = threading.Thread(target=server_thread, daemon=True)
    thread.start()

    # 等待本地测试 server 就绪
    deadline = time.monotonic() + _SERVER_START_TIMEOUT
    while time.monotonic() < deadline:
        if server is not None and server.transport is not None:
            break
        time.sleep(_LISTEN_POLL_INTERVAL)
    if server is None or server.transport is None:
        print("自检失败：本地测试 server 未就绪", file=sys.stderr)
        return 1

    def read_temp() -> float:
        """用 app 同款调用读取并换算温度"""
        result = client.read_input_registers(TEMP_REGISTER, count=1,
                                             device_id=args.slave_id)
        assert not result.isError(), result
        raw = result.registers[0]
        return (raw - 65536) / 10.0 if raw >= 32768 else raw / 10.0

    try:
        client = ModbusTcpClient("127.0.0.1", port=port)
        if not client.connect():
            print("自检失败：无法连接本地测试 server", file=sys.stderr)
            return 1

        # 1) 正常升温：两次读取温度递增
        t1 = read_temp()
        time.sleep(0.3)
        t2 = read_temp()
        assert t2 > t1, f"温度应递增: {t1} -> {t2}"
        print(f"  [自检] 正常升温: {t1:.1f}℃ -> {t2:.1f}℃  OK")

        # 2) 探头断开：返回 0x7FFF
        sim.probe_disconnect = True
        result = client.read_input_registers(TEMP_REGISTER, count=1,
                                             device_id=args.slave_id)
        assert result.registers[0] == PROBE_DISCONNECT_RAW, "断开标志应为 0x7FFF"
        print(f"  [自检] 探头断开: 0x{result.registers[0]:04X}  OK")
        sim.probe_disconnect = False

        # 3) 负温度：signed int16 × 10 换算正确
        sim.reset(start_temp=-5.0, rate_c_per_min=0.0)
        value = read_temp()
        assert abs(value - (-5.0)) < 1e-6, f"负温度换算错误: {value}"
        print(f"  [自检] 负温度: {value:.1f}℃  OK")

        client.close()
    except Exception as exc:
        print(f"自检失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None and server_loop is not None:
            future = asyncio.run_coroutine_threadsafe(server.shutdown(), server_loop)
            future.result(timeout=_SHUTDOWN_TIMEOUT)
        thread.join(timeout=_SHUTDOWN_TIMEOUT)

    print("  自检全部通过")
    return 0


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Modbus RTU 温度读取器模拟器（无真实硬件时测试 SantokrOCR 用）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port",
                        help="模拟器绑定的串口（com0com 虚拟串口对的一端，如 COM10）；--self-test 时无需提供")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="波特率")
    parser.add_argument("--slave-id", type=int, default=DEFAULT_SLAVE_ID,
                        help="从站地址")
    parser.add_argument("--start-temp", type=float, default=DEFAULT_START_TEMP,
                        help=f"起始温度（℃），默认 {DEFAULT_START_TEMP:.0f}")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE,
                        help="趋势变化速率（℃/min），升降同幅")
    parser.add_argument("--trend", choices=["down", "up"], default=DEFAULT_TREND,
                        help="初始趋势：down=下降（默认），up=上升；运行中按 R 或回车翻转")
    parser.add_argument("--step", type=float, default=DEFAULT_STEP,
                        help="采样步长（秒）")
    parser.add_argument("--noise", type=float, default=DEFAULT_NOISE,
                        help="高斯噪声幅度（℃），0 表示无噪声")
    parser.add_argument("--probe-disconnect", action="store_true",
                        help="模拟探头断开（恒返回 0x7FFF）")
    parser.add_argument("--disconnect-after", type=float, default=None,
                        help="运行 N 秒后模拟探头断开（返回 0x7FFF）")
    parser.add_argument("--verbose", action="store_true",
                        help="打印收发原始帧（调试用）")
    parser.add_argument("--self-test", action="store_true",
                        help="运行无串口自检（TCP 回环，不需要 com0com），验证温度模型与寄存器链路")
    return parser.parse_args()


def main() -> int:
    """程序入口"""
    # 兼容中文 Windows 控制台（默认 GBK 编码）：stdout 遇到无法编码的
    # 字符（如 ⚙）时转义输出而不是抛异常崩溃，保证重定向/后台运行时也稳。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="backslashreplace")
        except Exception:
            pass

    args = parse_args()
    if args.self_test:
        return run_self_test(args)
    try:
        return asyncio.run(run_server(args))
    except KeyboardInterrupt:
        print("\n模拟器已退出")
        return 0


if __name__ == "__main__":
    sys.exit(main())
