"""Web 配置 — 主进程与 Web 进程共用的配置优先级链

Web 进程（build/web/WebServer/WebServer.exe 或 python -m web.backend.server）
与主进程（app，端口探测/自动启动）读取同一组配置，须得到相同结果。

优先级链（从高到低，高优先级覆盖低优先级）：
  1. exe 旁 <app_base>/build/web/WebServer/web_config.yaml（最高，可写）
     —— 端口冲突一键切换时写入（save_web_config_port）
  2. <app_base>/config/web_config.yaml（fallback，git 忽略）
  3. <app_base>/config/web_config.yaml.example（模板默认值，随程序分发）

默认值由 config/web_config.yaml.example 提供（是文件而非代码常量）。
代码不做默认值兜底：链上无有效配置、或配置非法（host/port 缺失或不可解析）
时抛 WebConfigError，调用方报错并不启动。

app_base 由调用方按自身形态提供（本模块不自行推断）：
  - Web 进程：frozen → sys.executable 上溯三级（WebServer.exe 恒位于
    <app_base>/build/web/WebServer/）；dev → 仓库根
  - 主进程：frozen → 主 exe 目录；dev → 仓库根

配置按 section（web_server / ipc_socket）逐层覆盖合并：低级文件只提供
缺失的 section/键，高级文件对其覆盖。这样 exe 旁 config 即使只写 web_server
段，ipc_socket 仍能从低级文件继承，不会丢端口。
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_SECTIONS = ("web_server", "ipc_socket")


class WebConfigError(Exception):
    """Web 配置读取/校验失败——调用方应报错并不启动（不使用代码默认值兜底）"""


def web_exe_dir(app_base: Path) -> Path:
    """Web 进程安装目录：<app_base>/build/web/WebServer（固定路径，dev 与打包统一）"""
    return Path(app_base) / "build" / "web" / "WebServer"


def main_app_base() -> Path:
    """主进程侧 app_base（主程序根目录）

    - 打包（frozen）：主 exe 所在目录
    - dev：仓库根（web/backend/config.py → 向上两级）

    主进程（core.ipc_server / ui.camera_realtime_window）共用此函数，避免多处
    重复推导导致漂移。注意：与 Web 进程侧的 ipc_client.web_app_base() 不同
    （后者 frozen 时是 WebServer.exe 上溯三级）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _candidate_paths(app_base: Path) -> list:
    """配置优先级链路径（从高到低）。仅返回路径，是否存在的判断在读取时进行。"""
    base = Path(app_base)
    return [
        web_exe_dir(base) / "web_config.yaml",
        base / "config" / "web_config.yaml",
        base / "config" / "web_config.yaml.example",
    ]


def load_web_config(app_base: Path) -> Dict[str, Dict[str, Any]]:
    """沿优先级链读取配置，返回 {"web_server": {...}, "ipc_socket": {...}}

    低级先应用、高级后覆盖（逐层 update）。任一【已存在】的配置文件解析失败、
    或合并结果缺少/非法 host/port 时抛 WebConfigError（不做代码默认值兜底）。
    """
    cfg: Dict[str, Dict[str, Any]] = {"web_server": {}, "ipc_socket": {}}
    for path in reversed(_candidate_paths(app_base)):  # 低 → 高，高级覆盖低级
        if not path.exists():
            continue
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise WebConfigError(f"配置文件解析失败: {path}: {e}") from e
        if not isinstance(data, dict):
            raise WebConfigError(f"配置文件格式异常（非对象）: {path}")
        for section in _SECTIONS:
            sec = data.get(section)
            if isinstance(sec, dict):
                cfg[section].update(sec)

    result: Dict[str, Dict[str, Any]] = {}
    for section in _SECTIONS:
        host = cfg[section].get("host")
        port_raw = cfg[section].get("port")
        if not host or port_raw is None:
            raise WebConfigError(
                f"配置缺少 {section}.host/.port（{app_base} 下无有效 web 配置，"
                f"请检查 config/web_config.yaml(.example)）")
        if not isinstance(host, str):
            raise WebConfigError(f"配置 {section}.host 非法: {host!r}")
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            raise WebConfigError(f"配置 {section}.port 非法: {port_raw!r}")
        result[section] = {"host": host, "port": port}
    return result


def save_web_config_port(app_base: Path, port: int) -> Path:
    """把新的 web_server.port 写入 exe 旁 config（优先级链最高级）

    只写最小片段 {"web_server": {"port": N}}：其余键（ipc_socket、host 等）靠
    section 级合并从低级配置继承，避免把低级配置整体快照冻结、静默覆盖用户
    后续对 config/web_config.yaml 的修改。返回写入的路径。
    """
    path = web_exe_dir(app_base) / "web_config.yaml"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"web_server": {"port": int(port)}}, f,
                           allow_unicode=True, sort_keys=False)
    except OSError as e:
        # 目录只读/无权限等场景：抛出清晰错误，由调用方（main.py）友好提示
        raise OSError(f"无法写入端口配置 {path}: {e}") from e
    logger.info("已写入端口配置 %s: web_server.port=%s", path, port)
    return path
