"""IpcClient — Web 进程侧 TCP socket 客户端

Web 进程是纯转发层：收手机 HTTP 请求 → 通过 send_cmd 发给主进程 → 返回结果。
协议与 core/ipc_server.py 一致：UTF-8，每条消息以 \n 结尾，每次请求新建连接。

错误处理：主进程不可达 / 响应超时 / 响应解析失败统一抛 IpcError，
由 server.py 映射为 HTTP 502（主进程不可达）等状态码。
"""

import json
import logging
import socket
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from .config import load_web_config

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3.0           # 连接 + 收发超时（秒）


class IpcError(Exception):
    """主进程不可达 / 响应超时 / 响应协议错误"""


def web_app_base() -> Path:
    """Web 进程侧的 app_base

    - 打包（frozen）：WebServer.exe 恒位于 <app_base>/build/web/WebServer/，
      app_base = 可执行文件路径上溯三级（WebServer → web → build → app_base）
    - dev：仓库根（web/backend/ipc_client.py → 向上两级）
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[3]
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Dict[str, Any]]:
    """沿优先级链加载 Web 进程配置（链定义见 web.backend.config）

    返回 {"web_server": {"host", "port"}, "ipc_socket": {"host", "port"}}。

    与主进程 core.ipc_server.load_ipc_config 读取同一配置链，
    两端读取同一组配置须得到相同结果。
    """
    return load_web_config(web_app_base())


def send_cmd(cmd: dict, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """发送单条 IPC 命令，阻塞等待响应后关闭连接

    Args:
        cmd: IPC 命令 dict（如 {"cmd": "get_status"}）
        timeout: 连接与收发超时（秒）

    Returns:
        主进程返回的响应 dict（原样透传，不判断 ok）

    Raises:
        IpcError: 主进程不可达、响应超时或响应无法解析
    """
    cfg = load_config()
    ipc = cfg['ipc_socket']
    host, port = ipc['host'], ipc['port']

    try:
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
    except (socket.timeout, OSError) as e:
        raise IpcError(f"主进程不可达 ({host}:{port}): {e}") from e

    if not data:
        raise IpcError("主进程无响应（连接被关闭）")
    try:
        resp = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise IpcError(f"主进程响应解析失败: {e}") from e
    if not isinstance(resp, dict):
        raise IpcError(f"主进程响应格式错误: {resp!r}")
    return resp
