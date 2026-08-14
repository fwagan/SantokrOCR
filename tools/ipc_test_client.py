"""IPC 测试工具 — 模拟 Web 进程向主进程发送命令

用法：
    python tools/ipc_test_client.py get_status
    python tools/ipc_test_client.py start --heater 50 --fan 80
    python tools/ipc_test_client.py add_event --type 一爆开始 --offset 60
    python tools/ipc_test_client.py add_value_event --type 调整火力 --offset 240 --value 60
    python tools/ipc_test_client.py end --offset 732

ipc_socket 端口走配置优先级链（见 web.backend.config）。
"""

import argparse
import json
import socket
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ipc_server import load_ipc_config  # noqa: E402
from web.backend.config import WebConfigError  # noqa: E402


def send_cmd(cmd: dict, host: str, port: int, timeout: float = 3.0) -> dict:
    """发送单条命令，阻塞等待响应"""
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.settimeout(timeout)
        payload = json.dumps(cmd, ensure_ascii=False) + "\n"
        conn.sendall(payload.encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        return json.loads(data.decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="IPC 命令测试工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("get_status", help="查询当前温度/状态")
    sub.add_parser("get_checkpoints", help="查询 checkpoint 静态列表")

    p_start = sub.add_parser("start", help="入豆（开始烘焙）")
    p_start.add_argument("--heater", type=float, default=50.0, help="初始火力")
    p_start.add_argument("--fan", type=float, default=80.0, help="初始风门")

    p_ev = sub.add_parser("add_event", help="标记一次性事件")
    p_ev.add_argument("--type", required=True, help="事件类型")
    p_ev.add_argument("--offset", type=float, required=True, help="相对入豆偏移秒数")

    p_vev = sub.add_parser("add_value_event", help="标记带数值事件")
    p_vev.add_argument("--type", required=True, help="事件类型")
    p_vev.add_argument("--offset", type=float, required=True, help="相对入豆偏移秒数")
    p_vev.add_argument("--value", type=float, required=True, help="数值")

    p_end = sub.add_parser("end", help="烘焙结束")
    p_end.add_argument("--offset", type=float, required=True, help="相对入豆偏移秒数")

    args = parser.parse_args()

    try:
        cfg = load_ipc_config()
    except WebConfigError as e:
        print(f"[失败] 读取配置错误: {e}")
        sys.exit(1)
    host, port = cfg['host'], cfg['port']

    if args.cmd == "get_checkpoints":
        cmd = {"cmd": "get_checkpoints"}
    elif args.cmd == "get_status":
        cmd = {"cmd": "get_status"}
    elif args.cmd == "start":
        cmd = {"cmd": "start",
               "heater_initial": args.heater, "fan_initial": args.fan}
    elif args.cmd == "add_event":
        cmd = {"cmd": "add_event",
               "event": {"type": args.type, "offset": args.offset}}
    elif args.cmd == "add_value_event":
        cmd = {"cmd": "add_value_event",
               "event": {"type": args.type, "offset": args.offset,
                         "value": args.value}}
    elif args.cmd == "end":
        cmd = {"cmd": "end", "event": {"type": "烘焙结束", "offset": args.offset}}
    else:
        parser.error(f"未知命令: {args.cmd}")

    print(f"→ {host}:{port} {json.dumps(cmd, ensure_ascii=False)}")
    try:
        resp = send_cmd(cmd, host, port)
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        print(f"[失败] 连接失败: {e}")
        print("  请确认主进程实时识别窗口已打开（IPC server 已启动）")
        sys.exit(1)
    print(f"← {json.dumps(resp, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    main()
