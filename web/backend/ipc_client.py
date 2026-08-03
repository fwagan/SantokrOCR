"""IpcClient — Web 进程侧 TCP socket 客户端

Web 进程是纯转发层：收手机 HTTP 请求 → 通过 send_cmd 发给主进程 → 返回结果。
协议与 core/ipc_server.py 一致：UTF-8，每条消息以 \n 结尾，每次请求新建连接。

错误处理：主进程不可达 / 响应超时 / 响应解析失败统一抛 IpcError，
由 server.py 映射为 HTTP 502（主进程不可达）等状态码。
"""

import json
import logging
import socket
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3.0           # 连接 + 收发超时（秒）
DEFAULT_WEB_HOST = "0.0.0.0"    # web_server 段默认值
DEFAULT_WEB_PORT = 5000
DEFAULT_IPC_HOST = "127.0.0.1"  # ipc_socket 段默认值
DEFAULT_IPC_PORT = 9999

# 仓库根：web/backend/ipc_client.py → 向上三级
_REPO_ROOT = Path(__file__).resolve().parents[2]


class IpcError(Exception):
    """主进程不可达 / 响应超时 / 响应协议错误"""


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Dict[str, Any]]:
    """加载 Web 进程配置

    读取顺序：config/web_config.yaml → config/web_config.yaml.example → 默认值。
    返回 {"web_server": {"host", "port"}, "ipc_socket": {"host", "port"}}。

    注意：ipc_socket 段的回退顺序与默认值必须与 core/ipc_server.py 的
    load_ipc_config 保持一致（两端读取同一组配置须得到相同结果），改动需同步两端。
    """
    cfg = {}
    for name in ('web_config.yaml', 'web_config.yaml.example'):
        path = _REPO_ROOT / 'config' / name
        if not path.exists():
            continue
        try:
            import yaml
            with open(path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
            break
        except Exception:
            logger.warning("加载配置失败: %s，使用默认值", path)
    web = cfg.get('web_server') or {}
    ipc = cfg.get('ipc_socket') or {}
    return {
        'web_server': {
            'host': web.get('host', DEFAULT_WEB_HOST),
            'port': int(web.get('port', DEFAULT_WEB_PORT)),
        },
        'ipc_socket': {
            'host': ipc.get('host', DEFAULT_IPC_HOST),
            'port': int(ipc.get('port', DEFAULT_IPC_PORT)),
        },
    }


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
