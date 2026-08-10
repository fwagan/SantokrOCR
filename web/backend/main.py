"""Web 进程入口（GUI 版）— Mobile Event Marker 独立服务器

- 单实例：Windows 命名 mutex（端口被其他程序占用 ≠ 自己实例）
- 端口冲突：绑定失败 → 扫描空闲端口 → 弹窗「一键改用并启动」
  （写入 exe 旁 web_config.yaml）/「取消」→ 报错退出
- GUI（tkinter）：状态 + 手机访问 URL + 关闭按钮；关窗 = 停服务 + 退出
- 日志：uvicorn access log 关闭，其余写 exe 旁 webserver.log（GUI 不滚日志）

运行（dev）：python -m web.backend.main 或 python -m web.backend.server（从仓库根）
打包：build_web.spec 指向本文件 → build/web/WebServer/WebServer.exe

注：当前仅 GUI 版（--silent 静默模式未实现）。
"""

import ctypes
import logging
import socket
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from pathlib import Path

import uvicorn
from typing import Optional

# 注意：必须用绝对导入（web.backend.*）。本文件既是包内模块
# （python -m web.backend.main），也是 PyInstaller 入口脚本（作为 __main__ 运行，
# 无父包，相对导入会 ImportError）。
from web.backend.config import (WebConfigError, load_web_config,
                                save_web_config_port, web_exe_dir)
from web.backend.ipc_client import web_app_base
from web.backend.server import app as fastapi_app

logger = logging.getLogger(__name__)

_MUTEX_NAME = "Local\\SantokrOCR.WebServer"
_ERROR_ALREADY_EXISTS = 183       # Windows ERROR_ALREADY_EXISTS
_PORT_SCAN_LIMIT = 50             # 端口冲突时最多向后扫描的空闲端口数
_START_CHECK_INTERVAL_MS = 500    # 轮询 uvicorn 是否已监听的间隔
_START_CHECK_MAX_TRIES = 20       # 0.5s×20=10s 启动宽限，超时判启动失败

_mutex_handle: Optional[int] = None


# ── 单实例（命名 mutex） ──

def _create_mutex() -> bool:
    """创建命名 mutex。返回 True = 本实例持有；False = 已有实例在运行。"""
    global _mutex_handle
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        logger.error("创建 mutex 失败")
        return False
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


def _release_mutex() -> None:
    global _mutex_handle
    if _mutex_handle:
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None


# ── 端口探测 / 空闲扫描 ──

def _can_bind(host: str, port: int) -> bool:
    """host:port 当前能否绑定（预检，与 uvicorn 实际绑定行为一致）

    注意：不能设置 SO_REUSEADDR——Windows 上它允许第二个 socket 抢占已被
    占用的端口（预检会误判"可绑定"），导致端口冲突流程被跳过、uvicorn
    真正绑定才失败。uvicorn 绑定不使用 SO_REUSEADDR，此处保持一致。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
        return True
    except OSError:
        return False


def _find_free_port(host: str, start: int, limit: int) -> Optional[int]:
    """从 start 起向后扫描 limit 个端口，返回第一个空闲端口；无则 None"""
    for p in range(start, start + limit):
        if _can_bind(host, p):
            return p
    return None


def _local_ipv4s() -> list:
    """本机全部非回环 IPv4 地址（供手机访问 URL 展示）

    优先返回可经局域网访问的地址，剔除回环 / 链路本地 169.254.0.0/16 /
    基准测试保留段 198.18.0.0/15（VPN/代理常用，手机不可达，如 198.18.0.1）。
    主机名枚举为主、UDP 出口探测兜底。全部不可用时回退 127.0.0.1。
    """
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    # 兜底：UDP connect 取默认出口地址（不实际发包），覆盖主机名枚举遗漏
    for target in ("8.8.8.8", "223.5.5.5"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 80))
            ips.add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            s.close()
    ips.discard("127.0.0.1")

    def is_plausible(ip: str) -> bool:
        try:
            a, b, _, _ = (int(x) for x in ip.split("."))
        except ValueError:
            return False
        if a == 127:
            return False                      # 回环
        if a == 169 and b == 254:
            return False                      # 链路本地 169.254.0.0/16
        if a == 198 and b in (18, 19):
            return False                      # 基准测试保留段 198.18.0.0/15
        return True

    filtered = sorted(ip for ip in ips if is_plausible(ip))
    return filtered or sorted(ips) or ["127.0.0.1"]


# ── 日志 ──

def _setup_logging(app_base: Path) -> Path:
    """日志写 exe 旁 webserver.log；不输出到控制台/GUI。

    目录只读等场景降级为不写文件日志（windowed 应用无控制台，仅保证不崩溃）。
    """
    log_path = web_exe_dir(app_base) / "webserver.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_path),
            filemode="w",          # 每次启动截断
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)
    return log_path


# ── uvicorn 服务 ──

def _start_server(host: str, port: int):
    """在后台线程启动 uvicorn，返回 (server, thread)"""
    config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        access_log=False,     # 关闭 access log
        log_config=None,      # 不覆盖日志配置，交由 root（写文件）
        log_level="info",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True,
                              name="WebServer-uvicorn")
    thread.start()
    return server, thread


# ── 端口冲突弹窗（无主窗口，用隐藏 root 做父窗口） ──

def _hidden_root() -> tk.Tk:
    root = tk.Tk()
    root.withdraw()
    return root


def _dialog_ask_switch_port(port: int, new_port: int) -> bool:
    root = _hidden_root()
    try:
        return messagebox.askyesno(
            "端口被占用",
            f"端口 {port} 已被其他程序占用。\n\n"
            f"是否改用空闲端口 {new_port} 并启动？\n"
            f"（新端口将写入 exe 旁 web_config.yaml）",
            parent=root)
    finally:
        root.destroy()


def _dialog_info(title: str, msg: str) -> None:
    root = _hidden_root()
    try:
        messagebox.showinfo(title, msg, parent=root)
    finally:
        root.destroy()


def _dialog_error(title: str, msg: str) -> None:
    root = _hidden_root()
    try:
        messagebox.showerror(title, msg, parent=root)
    finally:
        root.destroy()


# ── GUI 窗口 ──

class ServerWindow(tk.Tk):
    """状态 + 手机访问 URL + 关闭按钮；关窗即停服务退出"""

    def __init__(self, host: str, port: int, server, thread: threading.Thread):
        super().__init__()
        self._server = server
        self._thread = thread
        self._port = port
        self._display_hosts = ([host] if host not in ("0.0.0.0", "")
                               else _local_ipv4s())
        self._check_tries = 0

        self.title("Mobile Event Marker 服务器")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._shutdown)

        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)

        self._status_var = tk.StringVar(value="状态：启动中…")
        ttk.Label(frame, textvariable=self._status_var,
                  font=("Microsoft YaHei", 11)).pack(anchor="w")

        urls = "\n".join(f"手机访问：http://{h}:{port}" for h in self._display_hosts)
        self._url_var = tk.StringVar(value=urls)
        ttk.Label(frame, textvariable=self._url_var,
                  font=("Microsoft YaHei", 11, "bold"),
                  foreground="#0a7", justify="left").pack(anchor="w", pady=(10, 0))

        ttk.Label(frame, text="关闭窗口即停止服务并退出。",
                  foreground="#888").pack(anchor="w", pady=(6, 16))

        ttk.Button(frame, text="关闭", command=self._shutdown).pack(anchor="e")

        self.after(_START_CHECK_INTERVAL_MS, self._check_started)

    def _check_started(self) -> None:
        if self._server.started:
            self._status_var.set(f"状态：运行中（端口 {self._port}）")
            return
        self._check_tries += 1
        if self._check_tries > _START_CHECK_MAX_TRIES:
            self._status_var.set("状态：启动失败")
            messagebox.showerror(
                "Web 服务启动失败",
                f"服务未能监听端口 {self._port}，请查看 webserver.log。",
                parent=self)
            self._shutdown()
            return
        self.after(_START_CHECK_INTERVAL_MS, self._check_started)

    def _shutdown(self) -> None:
        """停止 uvicorn → 释放 mutex → 退出"""
        self._server.should_exit = True
        self._thread.join(timeout=3)
        _release_mutex()
        self.destroy()


# ── 入口 ──

def main() -> None:
    """Web 进程主入口：单实例 → 端口解析 → GUI + uvicorn"""
    if not _create_mutex():
        _dialog_info("Web 服务已在运行",
                     "检测到另一个 Web 服务实例正在运行。\n请在已有实例的窗口中关闭它，或直接使用它。")
        return

    app_base = web_app_base()
    _setup_logging(app_base)
    try:
        cfg = load_web_config(app_base)
    except WebConfigError as e:
        _dialog_error("配置错误", f"{e}\n\nWeb 服务未启动。")
        _release_mutex()
        sys.exit(1)
    host = cfg["web_server"]["host"]
    port = cfg["web_server"]["port"]
    logger.info("启动 Web 服务，app_base=%s，host=%s，port=%s", app_base, host, port)

    if not _can_bind(host, port):
        new_port = _find_free_port(host, port + 1, _PORT_SCAN_LIMIT)
        if new_port is None:
            _dialog_error("无法启动 Web 服务",
                          f"端口 {port} 被占用，且未在 {port + 1}~{port + _PORT_SCAN_LIMIT} "
                          f"找到空闲端口。")
            _release_mutex()
            sys.exit(1)
        if not _dialog_ask_switch_port(port, new_port):
            _dialog_error("已取消启动", "未启动 Web 服务。")
            _release_mutex()
            sys.exit(1)
        try:
            save_web_config_port(app_base, new_port)
        except OSError as e:
            _dialog_error("无法切换端口", f"{e}\n\n未启动 Web 服务。")
            _release_mutex()
            sys.exit(1)
        logger.info("端口 %s 被占用，改用空闲端口 %s", port, new_port)
        port = new_port

    server, thread = _start_server(host, port)
    window = ServerWindow(host, port, server, thread)
    window.mainloop()


if __name__ == "__main__":
    main()
