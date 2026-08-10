"""主进程侧：探测/启动 Web 进程（自动启动便利，仅便利不监控不重启）

触发于「实时识别 + Web 事件标记」勾选时。只负责两件事：
1. 读有效端口（配置优先级链）用 /api/health 探测，已运行（确认是本服务）则跳过
2. 定位 <app_base>/build/web/WebServer/WebServer.exe（固定路径，dev 无则
   python -m web.backend.server 兜底）并拉起

不做启动结果检测——Web 进程启动后其窗口会出现在界面上，用户自行确认状态；
app 侧无等待、无通知。
"""

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

from .config import WebConfigError, load_web_config, web_exe_dir

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 0.5      # 单次健康探测超时（秒）


def web_process_exe(app_base: Path) -> Path:
    """Web 进程可执行文件路径：<app_base>/build/web/WebServer/WebServer.exe"""
    name = "WebServer.exe" if os.name == "nt" else "WebServer"
    return web_exe_dir(app_base) / name


_HEALTH_MARKER = b"mobile-event-marker"
_HEALTH_REQUEST = (b"GET /api/health HTTP/1.1\r\n"
                   b"Host: localhost\r\nConnection: close\r\n\r\n")


def _http_probe(host: str, port: int) -> bool:
    """HTTP 探测 /api/health 并校验服务标记，确认端口上是【我们的】Web 服务

    仅凭 TCP 连通会把端口上其他程序误判为"已在运行"（向用户传递错误信息），
    故校验响应体中的服务标记。
    """
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT) as s:
            s.settimeout(_PROBE_TIMEOUT)
            s.sendall(_HEALTH_REQUEST)
            data = b""
            while len(data) < 2048:
                chunk = s.recv(2048)
                if not chunk:
                    break
                data += chunk
            return _HEALTH_MARKER in data
    except OSError:
        return False


def _probe_web(app_base: Path) -> Optional[int]:
    """按当前配置探测 Web 服务（HTTP 健康检查）；确认是本服务则返回端口号，否则 None"""
    cfg = load_web_config(app_base)
    host = cfg["web_server"]["host"]
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    port = cfg["web_server"]["port"]
    if _http_probe(probe_host, port):
        return port
    return None


def ensure_web_running(app_base: Path) -> Tuple[bool, str]:
    """确保 Web 进程启动（自动启动便利，仅便利不监控不重启）

    已运行（health 确认是本服务）则跳过；未运行则拉起进程后立即返回，
    不做启动结果检测——启动后 Web 窗口自会显示，用户自行确认。
    返回 (是否已就绪?, 提示消息)。
    """
    try:
        running_port = _probe_web(app_base)
    except WebConfigError as e:
        return False, str(e)
    if running_port is not None:
        return True, f"Web 服务已在运行（端口 {running_port}）"

    exe = web_process_exe(app_base)
    if exe.exists():
        cmd = [str(exe)]
    elif getattr(sys, "frozen", False):
        return False, (f"未找到 Web 服务器组件（{exe}）。"
                       f"请重新安装程序或运行构建脚本生成后再试。")
    else:
        # dev：无 exe，用 python -m 兜底（cwd=仓库根保证 web 包可导入）
        cmd = [sys.executable, "-m", "web.backend.server"]

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(cmd, cwd=str(app_base), close_fds=True,
                         creationflags=creationflags)
    except OSError as e:
        logger.error("启动 Web 进程失败: %s", e)
        return False, f"启动 Web 进程失败: {e}"

    logger.info("已拉起 Web 进程: %s（不做结果检测，查看其窗口确认）", " ".join(cmd))
    return True, "Web 进程已启动（查看其窗口确认状态）"
