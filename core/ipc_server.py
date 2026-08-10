"""
IpcServer — 主进程侧 TCP socket 服务器

接收 Web 进程的 JSON 命令请求，分发到业务 handler，返回 JSON 响应。
协议：UTF-8，每条消息以 \n 结尾；请求 {"cmd": ...}，响应 {"ok": ...}。

传输层只负责收发，命令处理逻辑由外部 handler 提供（见 CameraRealtimeWindow）。
每个连接阻塞收发一次后关闭（Web 进程侧每次请求新建连接）。
"""

import json
import logging
import socket
import threading
from typing import Any, Callable, Dict, Optional

from web.backend.config import load_web_config, main_app_base

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999

_MAX_MESSAGE_LEN = 65536   # 单条消息上限（字节），防超大消息
_RECV_TIMEOUT = 5.0        # 单次请求接收超时（秒）
_ACCEPT_TIMEOUT = 0.5      # accept 轮询间隔，用于响应 stop()


def load_ipc_config() -> Dict[str, Any]:
    """沿优先级链加载 IPC socket 端口配置（链定义见 web.backend.config）

    与 web.backend.ipc_client.load_config 读取同一配置链，两端须得到相同结果。
    """
    ipc = load_web_config(main_app_base())['ipc_socket']
    return {'host': ipc['host'], 'port': ipc['port']}


class IpcServer:
    """TCP socket 服务器（后台线程）

    Args:
        handler: Callable[[dict], dict]，接收命令 dict，返回响应 dict。
                 在后台线程中调用，异常需自行兜底为错误响应。
        host: 监听地址（默认 127.0.0.1 仅本地）
        port: 监听端口
    """

    def __init__(self, handler: Callable[[dict], dict],
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self._handler = handler
        self._host = host
        self._port = port
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server_sock: Optional[socket.socket] = None
        self._on_bind_error: Optional[Callable[[Exception], None]] = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, on_bind_error: Optional[Callable[[Exception], None]] = None) -> None:
        """在后台线程启动服务器

        Args:
            on_bind_error: 端口绑定失败时回调（在主线程提示用户）
        """
        self._on_bind_error = on_bind_error
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="IpcServer")
        self._thread.start()

    def stop(self) -> None:
        """停止服务器（关闭监听 socket，等待 accept 超时退出）"""
        self._stop_event.set()
        sock = self._server_sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # ── 内部实现 ──

    def _serve(self) -> None:
        """监听主循环：接受连接 → 处理单次请求 → 关闭连接"""
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self._host, self._port))
            server.listen(5)
            server.settimeout(_ACCEPT_TIMEOUT)
            self._server_sock = server
            logger.info(f"IPC server 已启动: {self._host}:{self._port}")
        except OSError as e:
            logger.error(f"IPC server 端口绑定失败: {e}")
            if self._on_bind_error is not None:
                self._on_bind_error(e)
            return

        try:
            while not self._stop_event.is_set():
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._handle_connection(conn)
        finally:
            try:
                server.close()
            except OSError:
                pass
            self._server_sock = None

    def _handle_connection(self, conn: socket.socket) -> None:
        """读取一行 JSON 请求，调用 handler，写回 JSON 响应"""
        try:
            with conn:
                conn.settimeout(_RECV_TIMEOUT)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > _MAX_MESSAGE_LEN:
                        data = b""
                        break
                if not data.strip():
                    return

                try:
                    cmd = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(conn, {"ok": False, "error": "invalid json"})
                    return
                if not isinstance(cmd, dict):
                    self._send_json(conn, {"ok": False, "error": "invalid json"})
                    return

                try:
                    resp = self._handler(cmd)
                except Exception as e:
                    logger.exception("IPC handler 异常")
                    resp = {"ok": False, "error": str(e)}
                if not isinstance(resp, dict):
                    resp = {"ok": True, "result": resp}
                self._send_json(conn, resp)
        except Exception:
            logger.exception("IPC 连接处理异常")

    @staticmethod
    def _send_json(conn: socket.socket, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False) + "\n"
        conn.sendall(payload.encode("utf-8"))
