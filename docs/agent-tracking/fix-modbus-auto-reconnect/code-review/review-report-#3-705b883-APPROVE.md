# Code Review Report

## Scope

### Scope Resolution

- Review branch: `fix/modbus-auto-reconnect`
- Review ref: `refs/review/fix/modbus-auto-reconnect`
- Review ref existed: Yes (resolved to `aece581`)
- Starting point: `2e3e86c` (merge-base with `master`)
- Review range: `aece581..HEAD` for the incremental re-review (single commit `705b883`),
  plus a full `2e3e86c...HEAD` scan as explicitly requested by the caller
- First review: No (previous reports #1 and #2, both APPROVE)
- Uncommitted changes included: No (`git status --short` empty)
- Previous persistent report: `review-report-#2-aece581-APPROVE.md`
  (Address Findings: #1 FIX, #2 FIX, #3 DEFER)

### Scope Determination

The local review ref resolved to `aece581`, the commit covered by review #2, so the
actual new delta is `705b883`. The caller additionally asked to re-scan the whole branch
range `2e3e86c...HEAD`; that scan found no changes outside the files already reviewed in
reports #1/#2 plus the two new code changes of `705b883`. The gitignored local tests
(`Development/tests/test_modbus_reconnect.py`) and the installed pymodbus 3.13.1 sources
(`pymodbus/logging.py`, `pymodbus/client/serial.py`, `pymodbus/transport/transport.py`)
were read as verification context; they are not part of the diff. There were no staged or
unstaged changes.

## Confirmed Scope

```
# Incremental delta aece581..HEAD
 core/modbus_reader.py                              |  26 ++--
 .../review-report-#2-aece581-APPROVE.md            | 154 ++++++++++++++++++
 ui/camera_realtime_window.py                       |  12 ++-
 3 files changed, 187 insertions(+), 5 deletions(-)

# Full branch 2e3e86c...HEAD
 core/modbus_reader.py                              | 271 +++++++++++++++-------
 core/temperature_source.py                         |   5 -
 .../review-report-#1-7ca327e-APPROVE.md            | 148 +++++++++++++++
 .../review-report-#2-aece581-APPROVE.md            | 154 +++++++++++++++
 ui/camera_realtime_window.py                       |  27 ++-
 5 files changed, 494 insertions(+), 111 deletions(-)
```

## Review Report

## Verified behavior (commit `705b883`)

- **Prior finding #1 (pymodbus log spam + false recovery) — addressed.**
  `_on_reconnected()` is now called only from the `comm_ok` branch
  (`core/modbus_reader.py:184-188`), so a serial port that merely opens no longer flips
  the status. The reconnect branch (`:169-175`) leaves `_disconnected` True while `_connect()`
  succeeds but reads fail, and `_enter_disconnected()` early-returns before
  `_set_pymodbus_quiet(True)` (`:238-243`), so there is no log/status flip-flop. Verified
  every path: initial-connect failure, mid-roast disconnect, repeated failed reopens,
  pause/resume, stop, and loop exception all either reach the first good read (recovery) or
  exit with the `finally` restore (`:215-217`). No path leaves the reader permanently
  `_disconnected=True` with a working link except while paused, which resolves on resume.
- **Global logging mutation — normal paths are correct.** `pymodbus.logging` really is the
  single logger used by pymodbus 3.13.1 (`Log._logger = logging.getLogger(__name__)`,
  `pymodbus/logging.py:50`), and setting it to CRITICAL suppresses `Log.error/warning/info`
  while `Log.critical` still passes. Calling `_enter_disconnected()` twice cannot double-save
  because of the early return; `stop()` while disconnected restores the level and the
  `finally` restore is then a no-op; `stop()` before a disconnect is entered is also covered
  by `finally`. One narrow thread interleaving still leaks state — see finding 3.
- **Prior finding #2 (single transient failure clears the window) — addressed as specified.**
  With interval 0.25, `max_frames = 20` and `20/3 = 6.67`, so the window is cleared on the
  7th consecutive invalid frame; a valid frame resets the counter (`:999-1000`). The counter
  is reset on every new session/clear/finish via `_reset_web_state` (`:1497-1503`), and
  entering `waiting_charge` always goes through `_reset_for_new_session` → `_reset_web_state`.
  No off-by-one relative to the recorded user decision. Interaction with `_charged_pending`
  and `_process_charge` however exposes a new side effect — see finding 1.
- **No regression in the recorded-data contracts for the disconnected path.**
  `comm_disconnected` frames still only update `_latest_result`/indicators and never enter
  `results`, `data_table`, `stats_panel` or `_rolling_window`.
- **Tests** (`Development/tests/test_modbus_reconnect.py`, gitignored): the new
  `test_reconnect_status_waits_for_successful_read`,
  `test_ui_clears_rolling_window_only_after_threshold` and
  `test_ui_valid_frame_resets_invalid_counter` match the implemented semantics. They do not
  cover a tolerated gap followed by a charge, which is finding 1.
- **Report #2 Address Findings** committed in `705b883` matches the implementation for
  entries #1 and #2. Finding #3 (gitignored spec still mentions the deleted
  `reset_temperature_tracking` hook) remains DEFERRED by explicit user decision and is not
  re-flagged.

## Findings

[MEDIUM] 容忍瞬时断连后入豆仍会产生重复时间戳，scipy 重采样得到 NaN
**File**: ui/camera_realtime_window.py:987-997, 1039-1069, 1114-1117
**Issue**: 阈值修复让 1–6 个连续无效帧不再清空 `_rolling_window`，但占位帧在
`_update_rolling_window` 之前就 return（`:997`），窗口内容冻结而墙上时间继续前进。入豆时
`_process_charge` 仍按“窗口帧数 × 采样间隔 = 5s”假设补帧（`:1050-1051`），并用
`shift = charge_real - 5.0` 重映射（`:1045`）。一旦窗口内有被容忍的缺帧，窗口真实时间跨度
> 4.75s，最早帧重映射后为负，被 `_remap_result` 统一 clamp 到 0.0（`:1114-1117`）；当
`fill_count > 0` 时，最后一个填充帧与第一个窗口帧也会落在同一时间点。两种情况下结果表都会
出现重复 timestamp。`ui/statistics_panel.py:521` 用 scipy `interp1d` 重采样，重复 x 会让该
时间点返回 NaN（已用 scipy 实测：`RuntimeWarning: invalid value encountered in divide`，
结果 `nan`），随后 `savgol_filter`（默认 15s 窗口 / 0.25s 间隔 = 61 点，半径 30）把 NaN
扩散到前约 7.5s 的豆温曲线与 ROR。
**Impact Scenario**: 默认间隔 0.25s，`waiting_charge` 且窗口已满；入豆前 5s 内发生一次读取
超时（`_comm_failures=1`，`_client1` 仍有效，不触发清窗），随后恢复并按下入豆。窗口跨度为
5.0s，入豆帧在最后一个有效帧后一个采样点，`shift = charge - 5.0`，最早帧映射为 -0.25 →
0.0，次早帧映射为 0.0，于是结果表中出现两行 `timestamp=0.0`。`stats_panel.process_data()`
重采样后 t=0 处为 NaN，平滑后前约 7.5s 豆温/ROR 曲线出现断口，数据库保存后重新打开会话仍
重现该断口。这正是阈值方案选择保留数据后引入的新副作用（`aece581` 的“每次断连即清窗”不会
产生重复时间戳）。
**Fix**: 保证入豆批次的时间戳严格递增。最小改动是在 `_process_charge` 追加结果时跳过
`timestamp <= 上一帧 timestamp` 的帧（重复的 clamp 帧被丢弃）；更完整的方式是丢弃
`timestamp < charge_real - _EXTRA_RECORD_SECONDS` 的窗口帧后按实际最早帧时间重建填充前缀，
并补一个“单个缺帧 + 入豆”的测试。

  # BAD (当前): clamp 后直接 append
  for i, r in enumerate(frames):
      nr = self._remap_result(r, i)
      self.results.append(nr)

  # GOOD: 丢弃 clamp 产生的重复时间戳
  last_ts = -1.0
  for i, r in enumerate(frames):
      nr = self._remap_result(r, i)
      if nr['timestamp'] <= last_ts:
          continue
      last_ts = nr['timestamp']
      self.results.append(nr)

[MEDIUM] `_on_result_ui` docstring 仍描述“等待入豆时清空滚动窗口”，与阈值逻辑不符
**File**: ui/camera_realtime_window.py:980
**Issue**: `comm_disconnected` 的说明仍写“等待入豆时清空滚动窗口”，但当前实现只在连续无效帧
超过窗口容量 1/3 时清空（`:990-995`），少量瞬时失败时窗口保持不变。
**Impact Scenario**: 维护者按 docstring 认为每个断连占位帧都会清空窗口（`aece581` 的行为），
在排查入豆前数据、或分析上面的重复时间戳问题时会对 `_rolling_window` 的实际内容判断错误。
**Fix**: 改为“……；等待入豆时仅当连续无效帧超过窗口容量 1/3 才清空滚动窗口”。

[LOW] `_PYMODBUS_SAVED_LEVEL` 的保存/设置非原子，`stop()` 交错时可能永久屏蔽 pymodbus 日志
**File**: core/modbus_reader.py:41-50, 120, 216, 244
**Issue**: `_set_pymodbus_quiet` 用同一个模块级全局同时表示“保存的原级别”和“当前是否静音”，
且 `stop()`（UI 线程，`:120`）与 `_enter_disconnected()`（读取线程，`:244`）都无锁地修改它。
若 `stop()` 恰好执行在 `_PYMODBUS_SAVED_LEVEL = _PYMODBUS_LOGGER.level` 与
`setLevel(CRITICAL)` 之间，`stop()` 会恢复并清空保存值，随后读取线程把级别设为 CRITICAL；
循环退出时 `finally` 的恢复（`:216`）看到 `saved is None` 直接跳过，pymodbus 日志从此在整个
进程生命周期内保持 CRITICAL。另外该状态是模块级而非实例级：旧 reader 的 `finally` 可能在
新 reader 处于断连静音期间把级别恢复，导致静音失效（仅日志影响）。
**Impact Scenario**: 用户在断连瞬间点击停止，恰好命中保存与 `setLevel` 之间的极窄窗口；此后
该进程内所有 pymodbus 内部错误/警告被静默丢弃，后续串口故障排查会缺少关键日志（无功能影响，
概率很低）。
**Fix**: 用一个模块级 `threading.Lock` 包住“读取保存值 → setLevel → 清空保存值”的完整逻辑，
并提供同一个带锁的恢复入口给 `stop()`/`finally`；若允许 reader 实例重叠，优先按实例持有静音
状态或做引用计数，而不是单一全局保存值。

## Review Summary

| Severity | Count | Status |
|----------|-------|--------|
| CRITICAL | 0     | pass   |
| HIGH     | 0     | pass   |
| MEDIUM   | 2     | info   |
| LOW      | 1     | note   |

Verdict: APPROVE

---
⚠️ NOTICE TO CALLING AGENT: Do NOT apply any fixes from this review automatically.
Present this review to the user first and wait for explicit instruction on which
issues to address. The user must approve changes before any code is modified.
Present the full review output verbatim to the user. Do NOT summarize, 
compress, or reformat any findings. Paste the complete text as-is.
---

## Comment Tracking

- File: `ui/camera_realtime_window.py`
- Matching comment snippet: `- comm_disconnected：只更新状态显示，不写入任何结果数据；等待入豆时清空滚动窗口`
- Issue description: docstring describes unconditional clearing; HEAD clears only after the 1/3-window threshold. Reported as finding 2 of this report.
- Flagged commit: `705b883`
- Status: resolved（修复已应用，commit 待生成）
- Resolved commit: 下一提交

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | 容忍瞬时断连后入豆产生重复时间戳 / NaN | FIX | 入豆时丢弃早于 `charge_real - _EXTRA_RECORD_SECONDS` 的窗口帧，并按首个保留帧的实际烘焙时间重算前缀填充数，避免填充帧与保留帧撞时间；新增 `test_process_charge_drops_stale_window_frames` 与 `test_process_charge_empty_window_fills_full_prefix` |
| 2 | `_on_result_ui` docstring 与阈值逻辑不符 | FIX | 改为“等待入豆时连续无效帧超过窗口容量 1/3 才清空滚动窗口” |
| 3 | `_PYMODBUS_SAVED_LEVEL` 保存/恢复非原子 | ACCEPT | 概率极小，仅影响 pymodbus 内部日志，不引入全局锁 |

(Review #2 recorded no open comment issues.)

## Address Findings

| Issue # | Issue Short Description | User Decision | Details |
| ------- | ----------------------- | ------------- | ------- |
| 1 | 容忍瞬时断连后入豆产生重复时间戳，scipy interp1d 返回 NaN 并扩散到平滑曲线 |  |  |
| 2 | `_on_result_ui` docstring 仍写“等待入豆时清空滚动窗口”，未反映 1/3 阈值 |  |  |
| 3 | `_PYMODBUS_SAVED_LEVEL` 保存/设置非原子，`stop()` 交错时可能永久把 pymodbus 日志级别留在 CRITICAL |  |  |
