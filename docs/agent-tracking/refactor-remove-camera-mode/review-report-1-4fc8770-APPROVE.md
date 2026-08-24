# Code Review Report

## Scope

### Scope Resolution

- Review branch: `refactor/remove-camera-mode`
- Review ref: `refs/review/refactor/remove-camera-mode`
- Review ref existed: No (first review of this branch)
- Starting point: N/A (branch first review; focus was the uncommitted changes as requested by user)
- Review range: working tree + staged changes on `refactor/remove-camera-mode`
- First review: Yes
- Uncommitted changes included: Yes (staged deletions + unstaged modifications)
- Previous persistent report: None

### Scope Determination

User explicitly requested review of the uncommitted changes (this is a pre-commit review). Scope determined from `git status` + `git diff` + `git diff --cached`, excluding `.gitignore` (user's own change, out of scope per instruction).

## Confirmed Scope

```
 .gitignore                   |   3 +-   (out of scope — user's own change)
 core/modbus_reader.py        |   6 +-
 core/temperature_source.py   |   2 +-
 ui/camera_realtime_window.py | 797 +++---------------------------------
 utils/signal.py              |   2 +-
 core/camera_capture.py       | 223 ------- (deleted, staged)
 core/realtime_cache.py       | 189 ------- (deleted, staged)
```

Files actually reviewed: `ui/camera_realtime_window.py`, `core/modbus_reader.py`, `core/temperature_source.py`, `utils/signal.py`, plus deleted `core/camera_capture.py` / `core/realtime_cache.py` (original content via `git show HEAD:`).

## Review Report

### 审查结论

本重构删除 `ui/camera_realtime_window.py` 的 camera(摄像头/OCR) 数据源分支，modbus 实时识别保留为唯一路径。对用户指定的 7 项重点核查逐一验证，**未发现任何 bug 或问题**。全部检查通过，返回零 findings。

### 逐项核查确认

1. **`_on_result_ui` "其他状态"直接记录分支** — ✅ 保留且逻辑正确。
   - 分支位于 `ui/camera_realtime_window.py:1033-1037`，Web 协作关闭时 `_roast_state` 停在 `"idle"`（`_start_realtime_modbus` 第 877 行：`"waiting_charge" if self._web_enabled.get() else "idle"`），此时走到该分支直接记录。
   - 注释已从"摄像头模式"正确更新为"Web 协作关闭时 state 停在 idle"，与当前代码一致。
   - `_update_modbus_status()` 改为无条件调用（第 1021/1030/1037 行），函数仅读 `_modbus_cfg` + `_latest_result` 更新 Label 控件，无副作用；旧代码 `if self._data_source.get() == "modbus"` 恒真，行为无变化。所有调用点均在主线程（经 `_enqueue_ui` 调度）。

2. **`_get_active_source` 恒返回 `self._modbus_reader`** — ✅ 无 None 崩溃路径。
   - 全部 8 个调用方均判空：`_update_start_button`(470)、`_start_modbus_probe`(393)、`_pause_realtime`(910)、`_stop_realtime`(924)、`_clear_all_data`(943)、`_update_elapsed_time`(960)、`destroy`(1588)、`_on_closing`(1598)。每个都用 `if source and not source.is_stopped()` 或 `if not source: return` 守卫。

3. **`destroy()`** — ✅ 无悬空引用、无遗漏清理。
   - 已删 `_stop_preview()`、`_cap` 释放、`_cache.stop_writer()`、`_save_camera_rois()`。grep 确认 `_cap`/`_cache`/`rois`/`_preview*` 属性已全部移除，无残留引用。
   - 保留必要清理：source.stop()、`_stop_modbus_probe()`、`_stop_ipc_server()`。探测线程为 daemon 且 stop 事件置位后退出，`destroy` 先置 `_ui_queue=None`，探测线程后续 `_enqueue_ui` 静默返回（第 975 行 `if q is None: return`），无崩溃。

4. **`_create_ui` 布局** — ✅ 无逻辑错误。
   - 数据源选择行、摄像头控件栏、导出按钮、预览画布均已删除；`_modbus_ctrl_frame.pack(side="left")`(164) 与 `common_frame.pack(side="left")`(177) 按 pack 顺序正确排列；`_modbus_status_panel.pack(fill="both", expand=True)`(218) + `_realtime_status_frame.pack(side="bottom", fill="x")`(222) 布局与旧 modbus 分支一致。
   - 被删控件的引用（`_modbus_rb`/`_camera_rb`/`export_session_btn`/`roi_status_var`/`rotation_var`/`source_combo` 等）无任何残留。

5. **import 完整性** — ✅ 全部核实。
   - 已删除 import（cv2、PIL.Image/ImageTk、CameraProcessingThread、RealTimeProcessCache、VideoDigitExtractor、FrameViewer、get_cache_manager、FileOperations、Paths）均不再被使用。
   - 保留 import 均仍被使用：`numpy`（`_ipc_get_temp` 的 np.interp/np.isfinite）、`SlogSerializer`（`_load_ideal_slog`）、`build_checkpoints`（`_load_ideal_slog`/`_load_ideal_session`）、`Optional`（`_build_ideal_data` 返回类型）、`os`（`_load_ideal_slog` 的 os.path.basename）、`filedialog`（`_select_ideal_slog`）、`deepcopy`（`_on_modbus_probe_result`）、`EventType`（IPC 处理）、`queue`/`threading`/`traceback`/`tkfont` 等。
   - pyflakes 未安装，改用全量手动核对 + py_compile + 实际 import 验证。

6. **残留引用** — ✅ 干净。
   - `camera_realtime_window.py` 内对 `camera/preview/ROI/FrameViewer/export/_data_source/rois/extractor/processing_thread/_cache/_cap/_preview*` 的引用全部为 0（唯一例外 `_update_turnaround_cache` 是 modbus 回温功能方法，允许保留）。
   - 全仓库 `*.py` 对 `camera_capture`/`realtime_cache`/`CameraProcessingThread`/`RealTimeProcessCache` 无任何引用。
   - build.spec 未引用已删模块（仅引用保留的 `ui.recognition_window`）。
   - 保留的 `ui/recognition_window.py`（frame_viewer/roi_selector/cache_manager 的消费方）只 import 保留模块（cv2、VideoDigitExtractor、FrameViewer、get_cache_manager、FileOperations 均来自未删文件）。

7. **模块可导入** — ✅ 通过。
   - `python -m py_compile` 对 4 个改动文件全部通过。
   - `import ui.camera_realtime_window`、`import ui.recognition_window`、`import ui.dashboard`、`import main`、`import core.modbus_reader, core.temperature_source, utils.signal` 全部成功。

### 附加确认

- `core/modbus_reader.py` 的 `_check_anomaly` 新 docstring（"帧率归一化：连续无效帧越多，允许温差越大"）与实际实现一致（第 281 行 `gap = min(self._consecutive_invalid_frames + 1, _MAX_EFFECTIVE_GAP)`，阈值 = `gap * temp_diff_threshold`），非误导性注释。
- `core/temperature_source.py`、`utils/signal.py` 的 docstring 措辞清理准确（"供 ModbusReader 实现"、移除 camera_capture 引用），无残留。
- dashboard.py 仅使用 `_realtime_window` 的 `lift/focus_set/bind/_on_closing/winfo_exists`，均仍存在。
- `.gitignore` 改动（`docs/*` + `!docs/agent-tracking/`）为用户本人所为，按指令未纳入审查。

### 说明（非问题）

- `_main_pw` 赋值后未再读取（第 209 行）——HEAD 版本同样如此，属历史遗留，非本次重构引入，未列为 finding。
- `_stop_modbus_probe` 未 join 线程——daemon 线程 + stop 事件机制可安全退出，属既有设计，未列为 finding。

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0     | pass   |
| HIGH     | 0     | pass   |
| MEDIUM   | 0     | pass   |
| LOW      | 0     | pass   |

Verdict: APPROVE

---
⚠️ NOTICE TO CALLING AGENT: Do NOT apply any fixes from this review automatically.
Present this review to the user first and wait for explicit instruction on which
issues to address. The user must approve changes before any code is modified.
Present the full review output verbatim to the user. Do NOT summarize,
compress, or reformat any findings. Paste the complete text as-is.
---

## Comment Tracking

- No comment issues were raised in this review round (no findings). Empty tracking snapshot for this round.
- Comment wording changes in this round (camera_realtime_window.py `_on_result_ui` docstring, `_check_anomaly` docstring, temperature_source.py / signal.py module docstrings) were reviewed and found accurate against current code; not recorded as open issues.
