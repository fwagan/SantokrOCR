"""Web 后端 — FastAPI 纯转发层

手机浏览器 ←HTTP→ 本服务 ←TCP socket→ 主进程(app)。

- GET /api/status   → 转发 get_status（轮询温度/状态/回温 offset）
- GET /api/temp     → 转发 get_temp（offset 时刻的重采样豆温估算）
- POST /api/events  → 按 body 的 cmd 分发 start/add_event/add_value_event/end
- 静态挂载 web/frontend/dist（Phase 3 React 构建产物，目录存在才挂载）

运行：python -m web.backend.server（从仓库根）

注意：服务未鉴权，绑定 0.0.0.0，仅限可信局域网使用
（手机浏览器控制移动端的固有取舍，IPC socket 本身仅 localhost）。
"""

import json
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .ipc_client import IpcError, send_cmd

logger = logging.getLogger(__name__)

# POST /api/events 允许转发的命令（get_status 走 GET /api/status）
_VALID_CMDS = {"start", "add_event", "add_value_event", "end"}

app = FastAPI(title="Mobile Event Marker", version="0.2.0")


async def _forward(cmd: dict) -> dict:
    """转发 IPC 命令到主进程；主进程不可达/超时 → HTTP 502"""
    try:
        # send_cmd 是阻塞 socket 调用，放线程池避免阻塞事件循环
        return await run_in_threadpool(send_cmd, cmd)
    except IpcError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/status")
async def get_status():
    """转发 get_status：返回 {temp1, temp2, ror, state, turnaround_offset}

    主进程内部异常时兜底返回 {"ok": false, "error": "..."}，此处映射为 HTTP 500。
    正常响应不带 ok 字段，仅异常响应带 ok:false，可精确区分。
    """
    resp = await _forward({"cmd": "get_status"})
    if resp.get("ok") is False:
        raise HTTPException(status_code=500,
                            detail=resp.get("error") or "主进程内部错误")
    return resp


@app.get("/api/health")
async def health():
    """健康探测端点：确认本服务在监听（供主进程自动启动探测用，不转发 IPC）

    返回固定服务标记。主进程侧 launcher._http_probe 据此区分"我们的 Web 服务"
    与端口上其他程序——避免仅凭 TCP 连通就把别的程序误报为"已在运行"。
    """
    return {"service": "mobile-event-marker", "ok": True}


@app.get("/api/temp")
async def get_temp(offset: float = Query(..., ge=0,
                    description="相对入豆的秒数")):
    """转发 get_temp：返回该 offset 时刻的重采样豆温估算

    响应 {temp1: number|null}（null = 数据不足/超范围，前端显示 '--'）。
    主进程 ok:false（offset 非法）→ HTTP 400；主进程不可达由 _forward 兜底 502。
    """
    resp = await _forward({"cmd": "get_temp", "offset": offset})
    if resp.get("ok") is False:
        raise HTTPException(status_code=400,
                            detail=resp.get("error") or "参数错误")
    return resp


@app.post("/api/events")
async def post_event(request: Request):
    """按 body 的 cmd 分发 start/add_event/add_value_event/end

    业务失败（主进程返回 {"ok": false, "error": "..."}）原样透传，
    前端以 ok 字段判断成败；只有主进程不可达/超时才返回 502。
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    cmd = body.get("cmd")
    if cmd is None:
        raise HTTPException(status_code=400, detail="缺少 cmd 字段")
    if not isinstance(cmd, str) or cmd not in _VALID_CMDS:
        raise HTTPException(
            status_code=400,
            detail=f"未知 cmd: {cmd!r}，允许: {sorted(_VALID_CMDS)}",
        )
    return await _forward(body)


def _static_dist_dir() -> Path:
    """前端构建产物目录
    - 打包（frozen）：datas 打入 _MEIPASS/web/frontend/dist（onedir 下 _MEIPASS = _internal）
    - dev：web/frontend/dist
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "web" / "frontend" / "dist"
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _mount_static(target: FastAPI) -> None:
    """静态挂载 Phase 3 React 构建产物（目录存在才挂载）"""
    dist = _static_dist_dir()
    if not dist.is_dir():
        logger.warning("前端构建产物不存在，跳过静态挂载: %s", dist)
        return
    target.mount("/", StaticFiles(directory=str(dist), html=True), name="static")


_mount_static(app)


if __name__ == "__main__":
    # dev 运行：python -m web.backend.server（与 python -m web.backend.main 等价，
    # 走 main.py 的单实例 / 端口冲突 / GUI 逻辑）
    from .main import main

    main()
