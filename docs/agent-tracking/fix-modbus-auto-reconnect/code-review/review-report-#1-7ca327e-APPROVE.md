# Code Review Report

## Scope

### Scope Resolution

- Review branch: `fix/modbus-auto-reconnect`
- Review ref: `refs/review/fix/modbus-auto-reconnect`
- Review ref existed: No
- Starting point: `2e3e86c` (merge-base with `master`)
- Review range: `2e3e86c...HEAD` (single commit `7ca327e`)
- First review: Yes
- Uncommitted changes included: No (`git status --short` empty)
- Previous persistent report: None

### Scope Determination

`refs/review/fix-modbus-auto-reconnect` did not resolve, so this is the first review of the
branch. The only commit on the branch is `7ca327e` ("fix: auto-reconnect modbus reader after
USB disconnects"), whose parent is the `master` merge-base `2e3e86c`. No staged or unstaged
changes were present at review time. The gitignored design spec
(`docs/superpowers/specs/2026-10-25-modbus-auto-reconnect-design.md`) and the local test file
(`Development/tests/test_modbus_reconnect.py`) were read as context but are not part of the diff.

## Confirmed Scope

```
 core/modbus_reader.py        | 242 +++++++++++++++++++++++++------------------
 ui/camera_realtime_window.py |  11 ++
 2 files changed, 153 insertions(+), 100 deletions(-)
```

## Review Report

## Verified behavior (matches the design spec)

- Threshold/cadence: `_COMM_FAILURE_THRESHOLD = 3`, reconnect period
  `max(interval, 0.5)`, unbounded retries, counters reset on success
  (`core/modbus_reader.py:33-35,58,168-180`).
- Initial connect failure no longer emits `finished_signal`; it enters the retry state
  (`core/modbus_reader.py:129-137`).
- Pause blocks retries; `_stop_event.wait()` replaces `time.sleep()` in both the pause loop
  and the sampling wait, so stop interrupts promptly (`core/modbus_reader.py:141-143,185-189`).
- Comm loss never emits `finished_signal`, so `_on_finished_ui` cannot reset `_roast_state`
  during `roasting`; `finished_signal` is emitted only on loop exit/exception.
- Disconnected placeholder frames reach `_on_result_ui` but are dropped before
  `results` / `data_table` / `stats_panel` / `_rolling_window`; `_latest_result` and the
  indicator are still updated (`ui/camera_realtime_window.py:985-991`).
- `_client_lock` swaps references only; `client.close()` is called outside the lock, so the
  reader thread cannot be blocked by the close thread, and double-close is prevented by the
  atomic swap. `_run_loop`'s `finally` and the stop thread cannot close the same client.
- `temperature_diff` is fully removed from the Modbus reader, and the video/post-processing
  paths (`ui/recognition_window.py`, `ui/statistics_panel.py`, `ui/data_table.py`,
  `data/types.py`) still contain and require it.

## Findings

[MEDIUM] 断连期间 `waiting_charge` 的滚动窗口冻结，入豆后产生重复/回退时间戳
**File**: ui/camera_realtime_window.py:989-991, 1016-1022, 1024-1068, 1107-1110
**Issue**: `comm_disconnected` 占位帧在 `_on_result_ui` 提前返回，因此不再调用
`_update_rolling_window()`。断连期间 `_rolling_window` 保留断连前的最后 5 秒有效帧且不再前进。
重连后的首个有效帧被当作入豆帧时，`_process_charge()` 直接使用这份过期窗口，`_remap_result()`
把相对时间 `timestamp - (charge_real - 5.0)` 为负的帧统一 clamp 到 0.0。
**Impact Scenario**: 采样间隔 0.25s，t=100s 时设备断开（窗口为满 20 帧，t=95~99.75s），
断开期间手机按「入豆」（`_charged_pending=True`），t=112s 重连、首个有效帧作为入豆帧。
此时 `shift=107`，20 个窗口帧全部映射到 `timestamp=0.0`，于是结果表和数据库中出现 20 行
时间为 0.0、温度却来自断开前的旧数据，随后是 t=5.0 的入豆帧；若断开发生在窗口未填满时，
先写入的填充帧时间为 0~4.25s，随后窗口帧被 clamp 回 0.0，时间戳变为非单调，
`ui/slog_comparer.resample_data()`（`interp1d`）和 `_ipc_get_temp()`（`np.interp`）会基于
非单调 x 轴产生错误曲线/错误插值，手机端 `get_temp` 也会返回错误温度。
**Fix**: 断连帧到达且处于 `waiting_charge` 时清空 `_rolling_window`（让 `_process_charge`
走填充路径，全部使用入豆帧温度），或在 `_process_charge` 中丢弃
`f['timestamp'] < charge_real - _EXTRA_RECORD_SECONDS` 的窗口帧。

  # BAD (当前): 断连只影响入表，不影响窗口
  if result.get('comm_disconnected'):
      self._update_modbus_status()
      return

  # GOOD: 断连同样冻结/清空等待入豆窗口
  if result.get('comm_disconnected'):
      if self._roast_state == "waiting_charge":
          self._rolling_window = []
      self._update_modbus_status()
      return

[LOW] 重连分支缺少 stop 检查，停止后可能仍新建一次连接
**File**: core/modbus_reader.py:152-159
**Issue**: 重连条件只判断 `self._client1 is None` 和 `_next_reconnect_at`，未判断
`_stop_event`。`stop()` 关闭线程会异步把 `_client1` 置 None，而 `_next_reconnect_at`
在从未断连时仍是初始值 `0.0`。
**Impact Scenario**: 读取线程刚通过 `while not self._stop_event.is_set()` 检查、尚未到达
重连分支时用户点击停止；close 线程已把 `_client1` 置 None，读取线程随后执行 `_connect()`，
在停止之后重新打开串口、emit 一帧结果和「温度读取器已重新连接」。`finally` 最终会关闭该
client，不会泄漏端口，但会出现停止后的多余连接/日志/一帧 UI 更新。窗口很小（该区间无阻塞调用），
概率低。
**Fix**: 重连分支增加 `and not self._stop_event.is_set()`，或在 `_connect()` 入口检查 stop。

[LOW] 温差检测移除后实时路径残留死代码
**File**: ui/camera_realtime_window.py:278, 917
**Issue**: `_update_realtime_status` 仍判断 `result.get('abnormal_category') != 'temperature_diff'`，
但 Modbus 实时路径已不可能产生该值（恒为 `None`）；`_clear_all_data` 仍调用
`source.reset_temperature_tracking()`，而该覆写已删除，只剩基类空实现
（`core/temperature_source.py:62-63`，其文档字符串仍描述温差异常检测）。
**Impact Scenario**: 无功能影响，但后续维护者会误以为实时 Modbus 仍会产生
`temperature_diff`，或在排查该字段时被空实现误导。不影响任何运行行为。
**Fix**: 删除该判断条件与 `reset_temperature_tracking()` 调用（或保留调用但更新基类文档）。

[LOW] 无限重连期间错误日志按重试周期重复输出
**File**: core/modbus_reader.py:255-257, 290-292
**Issue**: `_create_client` 的异常路径每次重试都 `logger.error`；另外 pymodbus 3.13.1 的
`connect()` 内部失败时自身也会 `Log.error` 一行。重连不设上限且周期为 `max(interval, 0.5)`。
**Impact Scenario**: 采样间隔 1s、设备拔出 10 分钟 → 约 600 条 ERROR 日志（pymodbus 内部 + 
`_create_client`）。设计只约束了状态栏消息不重复，日志仍会刷屏，掩盖其他错误。当前应用未配置
文件日志（`logging.basicConfig` 仅在无关模块的 `__main__` 分支），影响主要体现在开发控制台。
**Fix**: 在状态转换时记录一次，或在重连失败时降级为 debug/按次数限流。

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0     | pass   |
| HIGH     | 0     | pass   |
| MEDIUM   | 1     | info   |
| LOW      | 3     | note   |

Verdict: APPROVE

---
⚠️ NOTICE TO CALLING AGENT: Do NOT apply any fixes from this review automatically.
Present this review to the user first and wait for explicit instruction on which
issues to address. The user must approve changes before any code is modified.
Present the full review output verbatim to the user. Do NOT summarize, 
compress, or reformat any findings. Paste the complete text as-is.
---

## Comment Tracking

- No comment issues found in this review.

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | 断连期间 waiting_charge 滚动窗口冻结导致入豆时间戳重复/回退 | FIX | 采用方案 A：断连帧到达且处于 waiting_charge 时清空 `_rolling_window`，重连后 `_process_charge` 走填充路径，时间轴保持 0~5s 连续 |
| 2 | 重连分支缺少 stop 检查 | FIX | 重连条件增加 `not self._stop_event.is_set()`，避免停止后再次建连 |
| 3 | 实时路径残留 temperature_diff / reset_temperature_tracking 死代码 | FIX | 删除 `_update_realtime_status` 的 temperature_diff 判断、`_clear_all_data` 的调用，以及基类空实现 |
| 4 | 无限重连期间错误日志重复输出 | FIX | 断连/重连状态转换各记一条 warning；逐次重试与读取失败日志降为 debug |
